import pandas as pd
import pytz
from datetime import datetime, timedelta

def load_and_parse_events(filepath: str) -> pd.DataFrame:
    """
    Loads events.csv, parses dates and times, and creates UTC start/end timestamps.
    """
    df = pd.read_csv(filepath)
    
    # Required columns per blueprint
    required_cols = [
        'event_id', 'event_type', 'release_date', 'release_time', 'timezone',
        'window_name', 'start_offset_seconds', 'end_offset_seconds',
        'symbols', 'priority', 'source', 'source_url', 'effective_date', 'notes'
    ]
    
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
            
    # Process each row
    parsed_events = []
    
    for _, row in df.iterrows():
        # Combine date and time
        dt_str = f"{row['release_date']} {row['release_time']}"
        
        # Localize to event timezone
        tz = pytz.timezone(row['timezone'])
        local_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        localized_dt = tz.localize(local_dt)
        
        # Convert to UTC
        utc_dt = localized_dt.astimezone(pytz.UTC)
        
        # Calculate window
        start_utc = utc_dt + timedelta(seconds=int(row['start_offset_seconds']))
        end_utc = utc_dt + timedelta(seconds=int(row['end_offset_seconds']))
        
        # Parse symbols
        symbols = [s.strip() for s in row['symbols'].split(',')]
        
        event_data = row.to_dict()
        event_data['anchor_utc'] = utc_dt
        event_data['start_utc'] = start_utc
        event_data['end_utc'] = end_utc
        event_data['parsed_symbols'] = symbols
        
        parsed_events.append(event_data)
        
    return pd.DataFrame(parsed_events)

if __name__ == "__main__":
    # Example usage
    pass
