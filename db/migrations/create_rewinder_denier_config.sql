-- Migration: Create rewinder_denier_config table
CREATE TABLE IF NOT EXISTS rewinder_denier_config (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    denier TEXT NOT NULL UNIQUE,
    mp_segundos FLOAT NOT NULL DEFAULT 37.0,
    tm_minutos FLOAT NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rewinder_denier_config_denier 
ON rewinder_denier_config(denier);

CREATE TRIGGER update_rewinder_denier_config_updated_at 
BEFORE UPDATE ON rewinder_denier_config
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
