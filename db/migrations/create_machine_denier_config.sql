-- Migration: Create machine_denier_config table
-- This table stores configuration parameters for each machine-denier combination

CREATE TABLE IF NOT EXISTS machine_denier_config (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    machine_id TEXT NOT NULL,
    denier TEXT NOT NULL,
    rpm INTEGER NOT NULL DEFAULT 0,
    torsiones_metro INTEGER NOT NULL DEFAULT 0,
    husos INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Unique constraint to prevent duplicate machine-denier combinations
    CONSTRAINT unique_machine_denier UNIQUE (machine_id, denier)
);

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_machine_denier_config_machine_id 
ON machine_denier_config(machine_id);

-- Add trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_machine_denier_config_updated_at 
BEFORE UPDATE ON machine_denier_config
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
