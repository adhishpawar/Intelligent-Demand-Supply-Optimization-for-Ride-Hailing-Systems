-- Round 3 stakeholder council (tech lead): the driver side has always had
-- ux_trips_one_active_per_driver (V001) as a database-level backstop against two
-- active trips on one driver, even if the application-level claim logic somehow
-- fails. The rider side had no equivalent -- nothing physically prevented one rider
-- from having multiple simultaneous live trips. The application-level guard
-- (TripRepository.find_active_for_rider, checked in TripService.request_ride) is
-- the primary defense; this index is the same "never trust a single layer" backstop
-- the driver side already relies on.
CREATE UNIQUE INDEX ux_trips_one_active_per_rider ON trips (rider_id)
    WHERE status IN ('REQUESTED', 'MATCHING', 'DRIVER_ASSIGNED', 'DRIVER_ARRIVING', 'DRIVER_ARRIVED', 'IN_PROGRESS');
