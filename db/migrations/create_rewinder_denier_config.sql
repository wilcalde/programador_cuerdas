-- Migration: Create rewinder_denier_config table
-- This table stores Mp and Tm parameters for each denier in the Rewinder process

CREATE TABLE IF NOT EXISTS rewinder_denier_config (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    denier TEXT NOT NULL UNIQUE,
    mp_segundos FLOAT NOT NULL DEFAULT 37.0,
    tm_minutos FLOAT NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_rewinder_denier_config_denier 
ON rewinder_denier_config(denier);

-- Add trigger to update updated_at timestamp
CREATE TRIGGER update_rewinder_denier_config_updated_at 
BEFORE UPDATE ON rewinder_denier_config
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
