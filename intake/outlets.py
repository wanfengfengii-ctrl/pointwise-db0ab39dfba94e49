from intake.source_store import require_name, source_write


def register_outlet(db, plant, outlet):
    require_name(plant, 'plant')
    require_name(outlet, 'outlet')
    with source_write(db):
        db.execute('''INSERT INTO outlets (plant, outlet) VALUES (?, ?)
            ON CONFLICT (plant, outlet) DO NOTHING''', (plant, outlet))


def find_outlet(db, plant, outlet):
    return db.execute(
        'SELECT 1 FROM outlets WHERE plant = ? AND outlet = ?', (plant, outlet)
    ).fetchone() is not None
