use crate::config::Config;
use anyhow::{anyhow, Result};
use sha2::{Digest, Sha256};

pub struct MempoolTxMetadata {
    pub txid: [u8; 32],
    pub fee_rate: f64,
    pub size: u32,
}

/// Dynamic fee threshold pruning
///
/// Filters out low-fee transactions that won't be included in next N blocks
pub struct FeeFilter {
    threshold: f64, // sat/vB
    enabled: bool,
    target_blocks: u32,
    filtered_count: u64,
    rpc_client: Option<reqwest::Client>,
    rpc_url: String,
    rpc_user: String,
    rpc_password: String,
}

impl FeeFilter {
    pub async fn new(config: &Config) -> Result<Self> {
        let client = Some(reqwest::Client::new());

        let mut filter = Self {
            threshold: 1.0, // Default 1 sat/vB
            enabled: config.fee_filter_enabled,
            target_blocks: config.fee_filter_blocks,
            filtered_count: 0,
            rpc_client: client,
            rpc_url: config.rpc_url.clone(),
            rpc_user: config.rpc_user.clone(),
            rpc_password: config.rpc_password.clone(),
        };

        // Fetch initial threshold
        if filter.enabled {
            if let Err(e) = filter.update_threshold().await {
                log::warn!("Failed to fetch initial fee threshold: {}", e);
            }
        }

        Ok(filter)
    }

    /// Check if transaction should be processed (passes fee filter)
    pub fn should_process(&mut self, fee_rate: f64) -> bool {
        if !self.enabled {
            return true;
        }

        if fee_rate >= self.threshold {
            true
        } else {
            self.filtered_count += 1;
            false
        }
    }

    /// Update fee threshold from Bitcoin Core RPC
    pub async fn update_threshold(&mut self) -> Result<()> {
        if !self.enabled || self.rpc_client.is_none() {
            return Ok(());
        }
        let result = self
            .rpc_call("estimatesmartfee", serde_json::json!([self.target_blocks]))
            .await?;

        if let Some(fee_rate_btc_kvb) = result["result"]["feerate"].as_f64() {
            // Convert BTC/kvB to sat/vB
            // 1 BTC = 100,000,000 sat
            // 1 kvB = 1000 vB
            let fee_rate_sat_vb = fee_rate_btc_kvb * 100_000_000.0 / 1000.0;
            self.threshold = fee_rate_sat_vb.max(1.0); // Minimum 1 sat/vB

            log::debug!("Updated fee threshold: {:.2} sat/vB", self.threshold);
        }

        Ok(())
    }

    pub async fn fetch_mempool_entry_from_raw_tx(
        &self,
        raw_tx: &[u8],
    ) -> Result<Option<MempoolTxMetadata>> {
        if self.rpc_client.is_none() {
            return Ok(None);
        }

        let (txid, txid_hex) = txid_from_raw_tx(raw_tx)?;
        let fallback_vsize = raw_tx.len() as u32;

        let entry = match self
            .rpc_call("getmempoolentry", serde_json::json!([txid_hex]))
            .await
        {
            Ok(entry) => entry,
            Err(e) if is_missing_mempool_entry(&e) => return Ok(None),
            Err(e) => return Err(e),
        };

        parse_mempool_entry(txid, fallback_vsize, &entry).map(Some)
    }

    pub async fn fetch_block_txids_from_zmq_hash(
        &self,
        block_hash: &[u8],
    ) -> Result<Vec<[u8; 32]>> {
        if self.rpc_client.is_none() {
            return Ok(Vec::new());
        }
        if block_hash.len() != 32 {
            return Err(anyhow!("ZMQ block hash must be 32 bytes"));
        }

        let block_hash_hex = hex_encode(block_hash);
        let block = match self
            .rpc_call("getblock", serde_json::json!([block_hash_hex, 2]))
            .await
        {
            Ok(block) => block,
            Err(e) if is_rpc_error_code(&e, -5) => {
                let mut reversed_hash = [0u8; 32];
                for (dst, src) in reversed_hash.iter_mut().zip(block_hash.iter().rev()) {
                    *dst = *src;
                }
                self.rpc_call(
                    "getblock",
                    serde_json::json!([hex_encode(&reversed_hash), 2]),
                )
                .await?
            }
            Err(e) => return Err(e),
        };
        let txs = block["result"]["tx"]
            .as_array()
            .ok_or_else(|| anyhow!("getblock response missing tx array"))?;

        let mut txids = Vec::with_capacity(txs.len());
        for tx in txs {
            let txid_hex = tx["txid"]
                .as_str()
                .ok_or_else(|| anyhow!("getblock transaction missing txid"))?;
            txids.push(decode_txid_hex(txid_hex)?);
        }
        Ok(txids)
    }

    async fn rpc_call(&self, method: &str, params: serde_json::Value) -> Result<serde_json::Value> {
        let client = self
            .rpc_client
            .as_ref()
            .ok_or_else(|| anyhow!("RPC client disabled"))?;
        let response = client
            .post(&self.rpc_url)
            .basic_auth(&self.rpc_user, Some(&self.rpc_password))
            .json(&serde_json::json!({
                "jsonrpc": "1.0",
                "id": "edge-daemon",
                "method": method,
                "params": params
            }))
            .send()
            .await
            .map_err(|_| anyhow!("Bitcoin Core RPC {} request failed", method))?;

        let result: serde_json::Value = response
            .json()
            .await
            .map_err(|_| anyhow!("Bitcoin Core RPC {} response parse failed", method))?;
        if !result["error"].is_null() {
            if let Some(code) = result["error"]["code"].as_i64() {
                return Err(anyhow!("Bitcoin Core RPC {} error code {}", method, code));
            }
            return Err(anyhow!("Bitcoin Core RPC {} error", method));
        }
        Ok(result)
    }

    pub fn threshold(&self) -> f64 {
        self.threshold
    }

    pub fn filtered_count(&self) -> u64 {
        self.filtered_count
    }

    pub fn reset_filtered_count(&mut self) {
        self.filtered_count = 0;
    }
}

fn parse_mempool_entry(
    txid: [u8; 32],
    fallback_vsize: u32,
    entry: &serde_json::Value,
) -> Result<MempoolTxMetadata> {
    let result = &entry["result"];
    let fee_btc = result["fees"]["base"]
        .as_f64()
        .ok_or_else(|| anyhow!("getmempoolentry response missing fees.base"))?;
    let vsize = result["vsize"].as_u64().unwrap_or(fallback_vsize as u64) as u32;
    if vsize == 0 {
        return Err(anyhow!("getmempoolentry response missing vsize"));
    }

    Ok(MempoolTxMetadata {
        txid,
        fee_rate: fee_btc * 100_000_000.0 / vsize as f64,
        size: vsize,
    })
}

fn txid_from_raw_tx(raw_tx: &[u8]) -> Result<([u8; 32], String)> {
    let stripped = stripped_tx_serialization(raw_tx)?;
    let first = Sha256::digest(&stripped);
    let second = Sha256::digest(first);
    let mut txid = [0u8; 32];
    for (dst, src) in txid.iter_mut().zip(second.iter().rev()) {
        *dst = *src;
    }
    let txid_hex = hex_encode(&txid);
    Ok((txid, txid_hex))
}

fn stripped_tx_serialization(raw_tx: &[u8]) -> Result<Vec<u8>> {
    let mut pos = 0usize;
    let mut stripped = Vec::with_capacity(raw_tx.len());

    stripped.extend_from_slice(read_bytes(raw_tx, &mut pos, 4)?);
    let has_witness =
        raw_tx.get(pos) == Some(&0) && matches!(raw_tx.get(pos + 1), Some(flag) if *flag != 0);
    if has_witness {
        pos += 2;
    }

    let input_count = read_varint(raw_tx, &mut pos, &mut stripped)?;
    for _ in 0..input_count {
        stripped.extend_from_slice(read_bytes(raw_tx, &mut pos, 36)?);
        let script_len = read_varint(raw_tx, &mut pos, &mut stripped)?;
        stripped.extend_from_slice(read_bytes(raw_tx, &mut pos, script_len as usize)?);
        stripped.extend_from_slice(read_bytes(raw_tx, &mut pos, 4)?);
    }

    let output_count = read_varint(raw_tx, &mut pos, &mut stripped)?;
    for _ in 0..output_count {
        stripped.extend_from_slice(read_bytes(raw_tx, &mut pos, 8)?);
        let script_len = read_varint(raw_tx, &mut pos, &mut stripped)?;
        stripped.extend_from_slice(read_bytes(raw_tx, &mut pos, script_len as usize)?);
    }

    if has_witness {
        for _ in 0..input_count {
            let item_count = read_varint_skip(raw_tx, &mut pos)?;
            for _ in 0..item_count {
                let item_len = read_varint_skip(raw_tx, &mut pos)?;
                read_bytes(raw_tx, &mut pos, item_len as usize)?;
            }
        }
    }

    stripped.extend_from_slice(read_bytes(raw_tx, &mut pos, 4)?);
    if pos != raw_tx.len() {
        return Err(anyhow!("transaction has trailing bytes"));
    }
    Ok(stripped)
}

fn read_bytes<'a>(bytes: &'a [u8], pos: &mut usize, len: usize) -> Result<&'a [u8]> {
    let end = pos
        .checked_add(len)
        .ok_or_else(|| anyhow!("transaction parse overflow"))?;
    if end > bytes.len() {
        return Err(anyhow!("transaction ended unexpectedly"));
    }
    let slice = &bytes[*pos..end];
    *pos = end;
    Ok(slice)
}

fn read_varint(bytes: &[u8], pos: &mut usize, stripped: &mut Vec<u8>) -> Result<u64> {
    let start = *pos;
    let value = read_varint_skip(bytes, pos)?;
    stripped.extend_from_slice(&bytes[start..*pos]);
    Ok(value)
}

fn read_varint_skip(bytes: &[u8], pos: &mut usize) -> Result<u64> {
    let tag = *read_bytes(bytes, pos, 1)?
        .first()
        .ok_or_else(|| anyhow!("transaction ended unexpectedly"))?;
    match tag {
        0x00..=0xfc => Ok(tag as u64),
        0xfd => Ok(u16::from_le_bytes(read_array(bytes, pos)?) as u64),
        0xfe => Ok(u32::from_le_bytes(read_array(bytes, pos)?) as u64),
        0xff => Ok(u64::from_le_bytes(read_array(bytes, pos)?)),
    }
}

fn read_array<const N: usize>(bytes: &[u8], pos: &mut usize) -> Result<[u8; N]> {
    let slice = read_bytes(bytes, pos, N)?;
    let mut out = [0u8; N];
    out.copy_from_slice(slice);
    Ok(out)
}

fn is_missing_mempool_entry(error: &anyhow::Error) -> bool {
    is_rpc_error_code(error, -5) || error.to_string().contains("not in mempool")
}

fn is_rpc_error_code(error: &anyhow::Error, code: i64) -> bool {
    let message = error.to_string();
    message.contains(&format!("code\":{}", code))
        || message.contains(&format!("code\": {}", code))
        || message.contains(&format!("code {}", code))
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn decode_txid_hex(hex: &str) -> Result<[u8; 32]> {
    if hex.len() != 64 {
        return Err(anyhow!("txid hex must be 64 characters"));
    }

    let mut txid = [0u8; 32];
    let bytes = hex.as_bytes();
    for i in 0..32 {
        txid[i] = (hex_nibble(bytes[i * 2])? << 4) | hex_nibble(bytes[i * 2 + 1])?;
    }
    Ok(txid)
}

fn hex_nibble(byte: u8) -> Result<u8> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' => Ok(byte - b'A' + 10),
        _ => Err(anyhow!("invalid txid hex")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fee_filter_disabled() {
        let mut filter = FeeFilter {
            threshold: 10.0,
            enabled: false,
            target_blocks: 1,
            filtered_count: 0,
            rpc_client: None,
            rpc_url: String::new(),
            rpc_user: String::new(),
            rpc_password: String::new(),
        };

        // Should process all transactions when disabled
        assert!(filter.should_process(1.0));
        assert!(filter.should_process(5.0));
        assert!(filter.should_process(100.0));
        assert_eq!(filter.filtered_count(), 0);
    }

    #[test]
    fn test_fee_filter_enabled() {
        let mut filter = FeeFilter {
            threshold: 10.0,
            enabled: true,
            target_blocks: 1,
            filtered_count: 0,
            rpc_client: None,
            rpc_url: String::new(),
            rpc_user: String::new(),
            rpc_password: String::new(),
        };

        // Should filter low-fee transactions
        assert!(!filter.should_process(5.0));
        assert!(!filter.should_process(9.9));
        assert!(filter.should_process(10.0));
        assert!(filter.should_process(20.0));

        assert_eq!(filter.filtered_count(), 2);
    }

    #[test]
    fn test_parse_mempool_entry() {
        let txid_hex = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        let entry = serde_json::json!({
            "result": {
                "vsize": 250,
                "fees": { "base": 0.000025 }
            },
            "error": null
        });

        let meta = parse_mempool_entry(decode_txid_hex(txid_hex).unwrap(), 300, &entry).unwrap();

        assert_eq!(meta.txid[0], 0x01);
        assert_eq!(meta.txid[31], 0xef);
        assert_eq!(meta.size, 250);
        assert!((meta.fee_rate - 10.0).abs() < 1e-9);
    }

    #[test]
    fn test_reject_bad_txid_hex() {
        assert!(decode_txid_hex("not-a-txid").is_err());
        assert!(decode_txid_hex(
            "zz23456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        .is_err());
    }

    #[test]
    fn test_genesis_coinbase_txid() {
        let raw_tx = hex_decode("01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff4d04ffff001d0104455468652054696d65732030332f4a616e2f32303039204368616e63656c6c6f72206f6e206272696e6b206f66207365636f6e64206261696c6f757420666f722062616e6b73ffffffff0100f2052a01000000434104678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5fac00000000").unwrap();
        let (txid, txid_hex) = txid_from_raw_tx(&raw_tx).unwrap();

        assert_eq!(
            txid_hex,
            "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"
        );
        assert_eq!(hex_encode(&txid), txid_hex);
    }

    fn hex_decode(hex: &str) -> Result<Vec<u8>> {
        if hex.len() % 2 != 0 {
            return Err(anyhow!("hex length must be even"));
        }
        let bytes = hex.as_bytes();
        let mut out = Vec::with_capacity(hex.len() / 2);
        for i in (0..bytes.len()).step_by(2) {
            out.push((hex_nibble(bytes[i])? << 4) | hex_nibble(bytes[i + 1])?);
        }
        Ok(out)
    }
}
