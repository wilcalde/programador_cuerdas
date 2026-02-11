-- Migration to create inventarios_cabuyas table
CREATE TABLE IF NOT EXISTS public.inventarios_cabuyas (
    id UUID PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    codigo TEXT UNIQUE NOT NULL,
    estado TEXT,
    grupo TEXT,
    existencia DOUBLE PRECISION DEFAULT 0,
    descripcion TEXT,
    inventario_seguridad DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Enable RLS (Recommended)
ALTER TABLE public.inventarios_cabuyas ENABLE ROW LEVEL SECURITY;

-- Create policy for all access (since other tables don't have RLS enabled or are simple)
-- Note: Replicating current project pattern where most tables don't have strict RLS enforced via policies yet
CREATE POLICY "Allow all access" ON public.inventarios_cabuyas FOR ALL USING (true);

-- Functions to update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_inventarios_cabuyas_updated_at
    BEFORE UPDATE ON public.inventarios_cabuyas
    FOR EACH ROW
    EXECUTE PROCEDURE update_updated_at_column();
