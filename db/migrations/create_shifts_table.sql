-- Create shifts table to store working hours per day
CREATE TABLE IF NOT EXISTS shifts (
    date DATE PRIMARY KEY,
    working_hours INTEGER NOT NULL CHECK (working_hours IN (8, 12, 16, 24)),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE shifts ENABLE ROW LEVEL SECURITY;

-- Allow public access (for now, as per app patterns)
CREATE POLICY "Public shifts access" ON shifts FOR ALL USING (true);
