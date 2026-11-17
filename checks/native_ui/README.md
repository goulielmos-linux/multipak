# Android checks

Scripts for the Waydroid integration that the regular tests cannot cover. The general
interface is checked by `tests/test_interface.py`.

- `verify_android*.py` and `verify_about_android.py` open the Android pages with sample
  data. Run them from the project root, with `QT_QPA_PLATFORM=offscreen` if there is no
  display.
- `prepare_instance_fixtures.py` (through `pkexec`) creates two disposable Android 13
  instances; `verify_live_instances.py` and `verify_instance_runtime.py` check them, and
  `cleanup_instance_fixtures.py` removes them. They never touch the system's own Waydroid.
  `prepare_android_11_fixture.py` and `verify_android_11.py` do the same for Android 11.
