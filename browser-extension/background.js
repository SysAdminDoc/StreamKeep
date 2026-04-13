// Service worker — no-op for now. Kept so we can wire a right-click
// context menu "Send to StreamKeep" in a future release without needing
// a manifest update to re-pair existing users.

chrome.runtime.onInstalled.addListener(() => {
  // Reserved for future context-menu / command registration.
});
