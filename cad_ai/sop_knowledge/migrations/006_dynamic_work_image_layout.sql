ALTER TABLE route_step
ADD COLUMN work_image_slots INTEGER NOT NULL DEFAULT 6
    CHECK(work_image_slots BETWEEN 1 AND 6);
