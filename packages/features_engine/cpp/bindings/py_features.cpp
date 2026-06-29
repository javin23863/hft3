/**
 * pybind11 binding for the hft3 C++ feature extraction stack.
 * Module name: hft3_features_cpp
 *
 * Replicates the exact composition used in feature_golden_main.cpp:
 *   FeatureExtractorCpp — wraps book, rolling counters, realized vol, regime filter.
 *   process_event() updates state (cheap per-event call).
 *   extract()        returns a (64,) float64 ndarray (copy from std::array<double,64>).
 *
 * Batch variant process_events() releases the GIL during the C++ loop so NumPy
 * arrays can be constructed in Python without contention.
 *
 * Action/side encoding (int8 arrays used by process_events):
 *   ACTION_CODES = {'ADD': 65, 'CANCEL': 67, 'MODIFY': 77, 'TRADE': 84}
 *   SIDE_CODES   = {'B': 66, 'A': 65}
 * These are the ASCII values of the single chars the C++ struct uses.
 */

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "feature_extractor.hpp"
#include "feature_index.hpp"

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace py = pybind11;

namespace {

// Map a Python action string to the single-char encoding the C++ struct uses.
inline char action_from_str(const std::string& s) {
    if (s == "ADD")    return 'A';
    if (s == "CANCEL") return 'C';
    if (s == "MODIFY") return 'M';
    if (s == "TRADE")  return 'T';
    throw std::invalid_argument("unknown action: " + s + " (expected ADD/CANCEL/MODIFY/TRADE)");
}

// Map a Python side string to char.
inline char side_from_str(const std::string& s) {
    if (s == "B" || s == "b") return 'B';
    if (s == "A" || s == "a") return 'A';
    throw std::invalid_argument("unknown side: " + s + " (expected B or A)");
}

}  // namespace

class PyFeatureExtractor {
public:
    explicit PyFeatureExtractor(double tick_size = 0.25,
                                int64_t rolling_window_ns = 1'000'000'000LL)
        : extractor_(tick_size, rolling_window_ns) {}

    // Single-event update — keeps the same argument signature as the Python
    // MBOFeatureExtractor.process_event() dataclass fields.
    void process_event(int64_t timestamp_ns,
                       int64_t order_id,
                       const std::string& action,
                       const std::string& side,
                       double price,
                       int32_t size) {
        hft::MBOEventCpp ev{timestamp_ns, order_id,
                            action_from_str(action),
                            side_from_str(side),
                            price, size};
        extractor_.process_event(ev);
    }

    // Returns the current 64-element feature vector as a (64,) float64 ndarray.
    py::array_t<double> extract() const {
        const auto& arr = extractor_.features();
        auto result = py::array_t<double>(64);
        auto buf = result.request();
        double* ptr = static_cast<double*>(buf.ptr);
        for (size_t i = 0; i < 64; ++i) {
            ptr[i] = arr[i];
        }
        return result;
    }

    // Batch update: parallel int8/int64/float64 arrays, one element per event.
    // action array: int8 with ASCII values (65='A'/ADD, 67='C'/CANCEL, 77='M'/MODIFY, 84='T'/TRADE)
    // side array:   int8 with ASCII values (66='B', 65='A')
    // Releases the GIL during the C++ loop so the caller can run numpy prep in parallel.
    void process_events(py::array_t<int64_t, py::array::c_style | py::array::forcecast> ts_arr,
                        py::array_t<int64_t, py::array::c_style | py::array::forcecast> oid_arr,
                        py::array_t<int8_t,  py::array::c_style | py::array::forcecast> action_arr,
                        py::array_t<int8_t,  py::array::c_style | py::array::forcecast> side_arr,
                        py::array_t<double,  py::array::c_style | py::array::forcecast> px_arr,
                        py::array_t<double,  py::array::c_style | py::array::forcecast> qty_arr) {
        auto ts_buf     = ts_arr.request();
        auto oid_buf    = oid_arr.request();
        auto action_buf = action_arr.request();
        auto side_buf   = side_arr.request();
        auto px_buf     = px_arr.request();
        auto qty_buf    = qty_arr.request();

        const py::ssize_t n = ts_buf.size;
        if (oid_buf.size != n || action_buf.size != n || side_buf.size != n ||
            px_buf.size != n  || qty_buf.size  != n) {
            throw std::invalid_argument("all arrays must have the same length");
        }

        const int64_t* ts_ptr  = static_cast<const int64_t*>(ts_buf.ptr);
        const int64_t* oid_ptr = static_cast<const int64_t*>(oid_buf.ptr);
        const int8_t*  act_ptr = static_cast<const int8_t*>(action_buf.ptr);
        const int8_t*  sid_ptr = static_cast<const int8_t*>(side_buf.ptr);
        const double*  px_ptr  = static_cast<const double*>(px_buf.ptr);
        const double*  qty_ptr = static_cast<const double*>(qty_buf.ptr);

        {
            py::gil_scoped_release release;
            for (py::ssize_t i = 0; i < n; ++i) {
                hft::MBOEventCpp ev;
                ev.timestamp_ns = ts_ptr[i];
                ev.order_id     = oid_ptr[i];
                ev.action       = static_cast<char>(act_ptr[i]);
                ev.side         = static_cast<char>(sid_ptr[i]);
                ev.price        = px_ptr[i];
                ev.size         = static_cast<int32_t>(qty_ptr[i]);
                extractor_.process_event(ev);
            }
        }
    }

    // Load event context from the events CSV.  Passes the csv path and optional
    // event_id directly to the EventContextEngineCpp constructor via
    // FeatureExtractorCpp::set_event_context (the context string, not the engine).
    // The C++ side already wires up EventContextEngineCpp at construction time
    // using the default CSV path.  This setter just overrides the resolved context
    // string for callers that want to manually force a context label (e.g. replay
    // that already knows the economic event window).
    void set_event_context(const std::string& context) {
        extractor_.set_event_context(context);
    }

    void reset() {
        extractor_.reset();
    }

private:
    hft::FeatureExtractorCpp extractor_;
};

PYBIND11_MODULE(hft3_features_cpp, m) {
    m.doc() = "hft3 C++ feature extraction — same compiled code as the live Rithmic path.";

    // Encoding maps documented at module level so Python callers can prepare
    // batch arrays without hard-coding magic numbers.
    m.attr("ACTION_CODES") = py::dict(
        py::arg("ADD")    = (int8_t)'A',   // 65
        py::arg("CANCEL") = (int8_t)'C',   // 67
        py::arg("MODIFY") = (int8_t)'M',   // 77
        py::arg("TRADE")  = (int8_t)'T'    // 84
    );
    m.attr("SIDE_CODES") = py::dict(
        py::arg("B") = (int8_t)'B',        // 66
        py::arg("A") = (int8_t)'A'         // 65
    );

    py::class_<PyFeatureExtractor>(m, "FeatureExtractor")
        .def(py::init<double, int64_t>(),
             py::arg("tick_size") = 0.25,
             py::arg("rolling_window_ns") = 1'000'000'000LL,
             "Create a feature extractor.\n"
             "tick_size         : instrument minimum tick (e.g. 0.25 for MES)\n"
             "rolling_window_ns : vol/flow accumulation window in nanoseconds")
        .def("process_event", &PyFeatureExtractor::process_event,
             py::arg("timestamp_ns"),
             py::arg("order_id"),
             py::arg("action"),
             py::arg("side"),
             py::arg("price"),
             py::arg("size"),
             "Feed a single MBO event into the extractor.\n"
             "action: 'ADD' | 'CANCEL' | 'MODIFY' | 'TRADE'\n"
             "side:   'B' (bid) | 'A' (ask)")
        .def("extract", &PyFeatureExtractor::extract,
             "Return current feature vector as np.ndarray shape (64,) float64.")
        .def("process_events", &PyFeatureExtractor::process_events,
             py::arg("ts"),
             py::arg("order_id"),
             py::arg("action"),
             py::arg("side"),
             py::arg("px"),
             py::arg("qty"),
             "Batch update from parallel arrays (GIL released during C++ loop).\n"
             "ts/order_id: int64  arrays\n"
             "action/side: int8   arrays — use ACTION_CODES / SIDE_CODES for encoding\n"
             "px:          float64 array\n"
             "qty:         float64 array (will be cast to int32 internally)")
        .def("set_event_context", &PyFeatureExtractor::set_event_context,
             py::arg("context"),
             "Override the event context label (e.g. 'EVENT_SHOCK', 'NORMAL').\n"
             "During live operation the extractor resolves context from events.csv;\n"
             "this setter lets replay code inject a pre-resolved label.")
        .def("reset", &PyFeatureExtractor::reset,
             "Reset all book and accumulator state.");
}
