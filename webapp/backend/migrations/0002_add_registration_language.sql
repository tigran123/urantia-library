-- Add language column to registration_requests so the approval/rejection
-- email helpers can render their body in the locale the requester used
-- at registration time. Existing rows get NULL; the email helpers treat
-- NULL as English.
ALTER TABLE registration_requests ADD COLUMN language VARCHAR;
