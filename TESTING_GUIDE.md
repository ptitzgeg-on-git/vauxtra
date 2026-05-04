# 🚀 Vauxtra UI Testing Guide

## ✅ System Status

All services running and ready:
- **Vauxtra Backend**: http://localhost:8888 (FastAPI)
- **Vauxtra Frontend**: http://localhost:5173 (React/Vite)
- **Lab Providers**: All 4 running and health-checked ✓
  - AdGuard: http://localhost:13000 (admin/adminadmin)
  - Pi-hole: http://localhost:18081 (admin/admin)
  - Technitium: http://localhost:15389 (admin/admin, zone: lab.test)
  - NPM: http://localhost:18082 (admin@example.com/admin)

## 📋 UI Testing Checklist

### Phase 1: Provider Configuration

**Objective**: Verify that Vauxtra can manage the 4 lab providers

1. **Navigate to Providers Page**
   - Open http://localhost:5173/providers
   - Should display 4 registered providers in a list
   - Each should show name, type, and URL

2. **Test Provider Connectivity**
   - Manually test each provider via dedicated page or health check
   - Look for "Connected" or status indicator
   - AdGuard, Pi-hole, Technitium, NPM should all show as operational

3. **Inspect Provider Details**
   - Click on each provider to view configuration
   - Verify URL, username are correctly displayed
   - Password should be masked/hidden for security

### Phase 2: DNS Records CRUD (AdGuard/Pi-hole/Technitium)

**Objective**: Test create, read, update, delete of DNS records through Vauxtra UI

#### AdGuard DNS Rewrite Test:
1. Open Providers → AdGuard (Lab)
2. Find "DNS Records" or "Rewrites" section
3. **CREATE**: Add new record
   - Domain: `test-vauxtra.lab.test`
   - Answer/IP: `10.20.30.40`
   - Click "Add"
4. **READ**: Verify record appears in list
5. **UPDATE**: Modify the record (change IP or domain)
6. **DELETE**: Remove the record
7. Verify all operations complete without errors

#### Pi-hole DNS Records Test:
Same as AdGuard but may use different UI labels. Pi-hole calls them "Local DNS Records".

#### Technitium DNS Records Test:
Same workflow for Technitium DNS provider.

### Phase 3: Proxy Hosts CRUD (NPM)

**Objective**: Test proxy host management through Vauxtra

1. Open Providers → NPM (Lab)
2. Find "Proxy Hosts" section
3. **CREATE**: Create new proxy host
   - Domain: `test-app.lab.test`
   - Target Host: `whoami` (the lab whoami container)
   - Target Port: `80`
   - Click "Create"
4. **READ**: Verify proxy host appears in list
5. **UPDATE**: Modify proxy host configuration
6. **DELETE**: Remove the proxy host
7. Verify all operations work smoothly

### Phase 4: Multilingual Support

**Objective**: Verify i18n functionality and auto-detection

#### 4.1 Language Auto-Detection:
1. Open browser console → check `localStorage.getItem('vauxtra_lang')`
2. If not set, browser language should be auto-detected on first load
3. Navigate to Settings → Language tab
4. Verify the flag for your system language is highlighted

#### 4.2 Language Selector Features:
1. Go to Settings → Language tab
2. **Verify UI Elements**:
   - Grid of language buttons with country flags
   - Each button shows flag emoji + language name
   - Currently selected language highlighted with primary color
3. **Search/Filter** (if implemented):
   - Check if there's a search box to filter languages
   - Try typing language name to filter options

#### 4.3 Supported Languages:
- 🇬🇧 English (en)
- 🇫🇷 Français (fr)
- 🇩🇪 Deutsch (de)
- 🇪🇸 Español (es)
- 🇧🇷 Português (pt)
- 🇳🇱 Nederlands (nl)
- 🇯🇵 日本語 (ja)
- 🇨🇳 中文 (zh)

#### 4.4 Language Switching Test:
1. Change language to French (Français)
   - UI should update immediately
   - All labels, buttons, dialogs in French
2. Perform a provider CRUD operation in French
   - Add a test DNS record while UI is in French
   - Verify success message is in French
3. Switch to another language (e.g., Spanish)
   - Verify all UI elements update
   - Toast notifications appear in new language
4. Switch back to English
   - Confirm UI returns to English
5. Refresh the page
   - Selected language should persist (from localStorage)

#### 4.5 Multilingual CRUD Test:
1. Switch to French
2. Go to Providers → AdGuard
3. Create a DNS record with a French name/label (if available)
4. Delete the record
5. Verify both operations show French success messages
6. Repeat with 2-3 different languages

### Phase 5: Edge Cases

1. **Network Error Handling**: Temporarily disconnect/block provider and verify error messages
2. **Concurrent Operations**: Try adding multiple records quickly (test race conditions)
3. **Large Domain Names**: Test DNS records with very long domain names
4. **Special Characters**: Test domain names with special characters (underscores, hyphens)
5. **Language Persistence**: Switch languages, navigate away, return to app → should remember language

## 📊 Success Criteria

✅ **Phase 1**: All 4 providers visible and configured
✅ **Phase 2**: CRUD operations work for DNS records (all 3 DNS providers)
✅ **Phase 3**: CRUD operations work for NPM proxy hosts
✅ **Phase 4**: Language selector visible, switching changes UI, languages persist
✅ **Phase 5**: App handles errors gracefully

## 🐛 Known Issues / Notes

1. **Provider Health Check**: API endpoint returns "down" status, but direct provider test passes
   - This appears to be an issue with the health check endpoint, not the providers themselves
   - Providers themselves work correctly when tested directly

2. **Initial Setup**: Setup wizard was bypassed using API script
   - Alternatively, can use full UI wizard if desired

## 📸 Screenshots / Observations

(To be filled during manual testing)

---

**Ready to test**: Navigate to http://localhost:5173 and follow the checklist above!
