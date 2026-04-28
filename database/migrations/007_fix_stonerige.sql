-- Poprawka literówki: Stonerige -> Stoneridge w polu firmware_tacho
UPDATE service_records
SET firmware_tacho = REPLACE(firmware_tacho, 'Stonerige', 'Stoneridge')
WHERE firmware_tacho LIKE '%Stonerige%';
