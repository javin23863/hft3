use anyhow::Result;
use zmq::Context;

/// ZMQ subscriber for Bitcoin Core notifications
pub struct ZmqSubscriber {
    socket: zmq::Socket,
}

impl ZmqSubscriber {
    /// Connect to Bitcoin Core ZMQ endpoints
    pub fn connect(zmq_rawtx: &str, zmq_rawblock: &str) -> Result<Self> {
        let ctx = Context::new();
        let socket = ctx.socket(zmq::SUB)?;
        
        // Subscribe to raw transactions
        socket.connect(zmq_rawtx)?;
        socket.set_subscribe(b"rawtx")?;
        
        // Subscribe to block hashes
        socket.connect(zmq_rawblock)?;
        socket.set_subscribe(b"hashblock")?;
        
        // Set high water mark (queue size)
        socket.set_rcvhwm(10000)?;
        
        log::info!("ZMQ subscriber connected to {} and {}", zmq_rawtx, zmq_rawblock);
        
        Ok(Self { socket })
    }
    
    /// Receive multipart message (topic + data)
    pub fn recv_multipart(&self) -> Result<Vec<Vec<u8>>> {
        let parts = self.socket.recv_multipart(0)?;
        Ok(parts)
    }
    
    /// Set receive timeout
    pub fn set_timeout(&self, timeout_ms: i32) -> Result<()> {
        self.socket.set_rcvtimeo(timeout_ms)?;
        Ok(())
    }
}

// Note: SO_TIMESTAMPNS implementation is platform-specific and requires
// unsafe code to access the underlying file descriptor. For now, we use
// the ZMQ message timestamp or system time as a fallback.
// 
// Future enhancement: Implement SO_TIMESTAMPNS on Linux for nanosecond
// precision kernel timestamps.
