-- Closing a customer without deleting one.
--
-- 673 work orders, 141 appointments, complaints, returns and quotes all point
-- at accounts(id). Deleting a customer who stopped trading orphans every one
-- of them, and the history of what somebody bought is the reason you can
-- answer a warranty claim two years later.
--
-- So a closed customer keeps their row and stops appearing on the book. The
-- same rule product retirement follows.
ALTER TABLE accounts ADD COLUMN closed_on TEXT;
ALTER TABLE accounts ADD COLUMN closed_why TEXT;

-- Where a lead came from, once it becomes a customer. Without this the signal
-- that earned the call is lost at the moment it pays off, and nobody can tell
-- which hunting run is worth the search spend.
ALTER TABLE accounts ADD COLUMN won_from_prospect TEXT;
