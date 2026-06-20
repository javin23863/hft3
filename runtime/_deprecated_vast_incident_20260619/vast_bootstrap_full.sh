#!/usr/bin/env bash
set -euo pipefail
mkdir -p /root/hft3
if [[ ! -d /root/hft3/repo/.git ]]; then
  rm -rf /root/hft3/repo
  git clone --branch cursor/vast-vbt-workflow https://github.com/javin23863/hft3.git /root/hft3/repo
fi
cd /root/hft3/repo
git fetch origin cursor/vast-vbt-workflow
git checkout cursor/vast-vbt-workflow
git pull --ff-only origin cursor/vast-vbt-workflow || true
mkdir -p /root/hft3/repo/data
ln -sfn /data/npz /root/hft3/repo/data/npz
export HFT3_NPZ_ROOT=/data/npz
export HFT3_MANIFEST_PATH=/data/npz/manifest.json
export HFT3_FEATURE_BACKEND=cpp
export PYTHONPATH=/root/hft3/repo/packages/features_engine/src:/root/hft3/repo/packages:/root/hft3/repo/apps/workbench:/root/hft3/repo:/root/hft3/repo/build
bash scripts/install_vbt_hbt_handoff_verify_deps.sh
pip3 install 'vectorbt[rust]==1.0.0' -q
pip3 install pybind11 -q
PYBIND_DIR="$(python3 -m pybind11 --cmakedir)"
cmake -B build -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR="$PYBIND_DIR"
cmake --build build --target hft3_features_cpp -j "$(nproc)"
python3 -c "import hft3_features_cpp; print('cpp_ok')"
python3 -c "from features._cpp_loader import load_cpp_features; load_cpp_features(); print('load_cpp_features_ok')"
python3 -c "import vectorbt; print('vbt', vectorbt.__version__)"
echo HEAD=$(git rev-parse --short HEAD)
echo NPZ=$(find /data/npz -maxdepth 1 -name '*.npz' | wc -l)
