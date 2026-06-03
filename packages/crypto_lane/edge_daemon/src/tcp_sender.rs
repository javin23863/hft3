use anyhow::Result;
use prost::Message;
use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;
use crate::edge_features::EdgeFeaturePacket;

/// TCP sender for streaming packets to Chicago
pub struct TcpSender {
    stream: TcpStream,
    addr: String,
}

impl TcpSender {
    /// Connect to Chicago receiver
    pub async fn connect(addr: &str) -> Result<Self> {
        let stream = TcpStream::connect(addr).await?;
        
        // Set TCP_NODELAY for low latency
        stream.set_nodelay(true)?;
        
        // Set keepalive
        let socket = socket2::SockRef::from(&stream);
        let keepalive = socket2::TcpKeepalive::new()
            .with_time(std::time::Duration::from_secs(60))
            .with_interval(std::time::Duration::from_secs(10));
        socket.set_tcp_keepalive(&keepalive)?;
        
        Ok(Self {
            stream,
            addr: addr.to_string(),
        })
    }
    
    /// Send a feature packet with length prefix
    pub async fn send_packet(&mut self, packet: &EdgeFeaturePacket) -> Result<()> {
        // Encode packet to bytes
        let mut buf = Vec::with_capacity(packet.encoded_len());
        packet.encode(&mut buf)?;
        
        // Write length prefix (4 bytes, big-endian)
        let len = buf.len() as u32;
        self.stream.write_all(&len.to_be_bytes()).await?;
        
        // Write packet data
        self.stream.write_all(&buf).await?;
        
        // Flush to ensure immediate transmission
        self.stream.flush().await?;
        
        Ok(())
    }
    
    /// Reconnect to Chicago
    pub async fn reconnect(&mut self) -> Result<()> {
        self.stream = TcpStream::connect(&self.addr).await?;
        self.stream.set_nodelay(true)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;
    use tokio::io::AsyncReadExt;
    
    #[tokio::test]
    async fn test_tcp_sender_connect() {
        // Start a test server
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut buf = [0u8; 1024];
            let n = socket.read(&mut buf).await.unwrap();
            assert!(n > 0);
        });
        
        // Connect sender
        let sender = TcpSender::connect(&addr.to_string()).await;
        assert!(sender.is_ok());
        
        server.await.unwrap();
    }
    
    #[tokio::test]
    async fn test_send_packet() {
        // Start a test server
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            
            // Read length prefix
            let mut len_buf = [0u8; 4];
            socket.read_exact(&mut len_buf).await.unwrap();
            let len = u32::from_be_bytes(len_buf) as usize;
            
            // Read packet
            let mut buf = vec![0u8; len];
            socket.read_exact(&mut buf).await.unwrap();
            
            // Decode packet
            let packet = EdgeFeaturePacket::decode(&buf[..]).unwrap();
            assert_eq!(packet.sequence_number, 42);
            assert_eq!(packet.fee_mean_sat_vb, 10.5);
        });
        
        // Connect and send
        let mut sender = TcpSender::connect(&addr.to_string()).await.unwrap();
        
        let packet = EdgeFeaturePacket {
            timestamp_ns: 1000,
            sequence_number: 42,
            fee_mean_sat_vb: 10.5,
            fee_stddev_sat_vb: 2.0,
            fee_zscore_latest: 1.5,
            fee_sample_count: 100,
            fee_p20: 5.0,
            fee_p40: 8.0,
            fee_p60: 12.0,
            fee_p80: 20.0,
            mempool_tx_count: 5000,
            mempool_bytes: 10_000_000,
            blockspace_stress_score: 0.5,
            deltas: vec![],
            min_fee_threshold: 5.0,
            filtered_tx_count: 100,
            delta_count: 0,
            uptime_seconds: 3600,
            packets_sent: 1,
            bytes_sent: 100,
        };
        
        sender.send_packet(&packet).await.unwrap();
        
        server.await.unwrap();
    }
}
