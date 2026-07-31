# Pico Browser Bridge

This is a local, read-only Chrome extension for Pico's `browser-review`
profile. It only receives Chrome's `activeTab` permission when you press its
popup button. It does not request access to browser history, cookies,
passwords, downloads, all tabs, or remote hosts.

## Load locally

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this folder.
4. In Pico at `http://127.0.0.1:18792`, open **Operations**, select
   **Browser review**, and choose **Create session code**.
5. Open the tab you want Pico to read, click the extension, paste the code,
   and choose **Share current tab**.

The code expires after five minutes and can be used once. Pico stores a
redacted, bounded visible-text snapshot for that one session. Revoke the share
from Operations when the work is done.
