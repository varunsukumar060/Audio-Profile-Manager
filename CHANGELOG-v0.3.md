# Audio Profile Manager v0.3

## Added

- Back up all saved audio profiles into one versioned JSON file.
- Restore profiles from a full backup through the development GUI.
- Validate backup metadata, profile counts, IDs, and profile configuration before restoring.
- Preserve existing profiles and assign unique IDs when restored profiles collide.
- Create a recovery backup before restoring when saved profiles already exist.
- Confirm restoration in the GUI and refresh the profile selector afterward.

## Safety

- Profile backup and restoration do not apply profiles or change active audio routing.
- Runtime routing state is not included in the portable profile backup.
- Existing profile files are not intentionally overwritten by restoration.

## Verification

- All 19 unit tests passed.
- The development GUI backup and restore workflow was manually tested successfully.
