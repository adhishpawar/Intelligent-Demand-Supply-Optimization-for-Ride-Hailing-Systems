-- Round 7 stakeholder council: `vehicles.vehicle_type` (SEDAN/HATCHBACK/SUV/AUTO/
-- BIKE) has existed since V001, and tools/seed.py already gives the 12 seeded
-- drivers a deliberately diverse spread across all five types -- but nothing in
-- matching, pricing, or the rider UI ever let a rider choose a ride type, or
-- differentiated a driver's candidacy or a trip's fare by it. The data model was
-- built for this feature; it was simply never wired up. This column is what a rider
-- actually asked for, independent of which specific driver/vehicle ultimately
-- fulfils it.
ALTER TABLE trips ADD COLUMN vehicle_type_requested VARCHAR(20) NOT NULL DEFAULT 'SEDAN'
    CHECK (vehicle_type_requested IN ('SEDAN', 'HATCHBACK', 'SUV', 'AUTO', 'BIKE'));
