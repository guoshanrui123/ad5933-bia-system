# Repository workflow

- Before every future push, report the changes and test results to the owner and wait for explicit approval for that push. Never automatically push, force-push, or change repository visibility.
- This is a curated release copy of the original AD5933 development project. Do not blindly overwrite packaging/path adaptations when importing later changes.
- Keep human raw data, clinical workbooks, participant identifiers, photos/videos, credentials and build artifacts out of Git, even in this private repository.
- Keep Python tools together: sibling imports and `with_name()` dependencies require this layout.
- Run `python tools/verify_baseline.py` and the relevant CLI smoke tests. Build firmware with `firmware/BUILD.ps1`. Hardware/clinical validation cannot be inferred from compilation or bench tests.
- Frozen CAL01–CAL08 fit the calibration; VALID01–VALID03 must stay independent.
