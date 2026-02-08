-- Create shifts table to store working hours per day
CREATE TABLE IF NOT EXISTS shifts (
    date DATE PRIMARY KEY,
    working_hours INTEGER NOT NULL CHECK (working_hours IN (8, 12, 16, 24)),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE shifts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public shifts access" ON shifts FOR ALL USING (true);
