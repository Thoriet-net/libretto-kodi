# -*- coding: utf-8 -*-
import os
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")

# ----- helpers -----
def ls(x, fallback=""):
    """
    Safe localization: accepts int or str, returns localized string if exists,
    otherwise returns fallback (or str(x)).
    """
    try:
        if isinstance(x, str) and x.isdigit():
            x = int(x)
        if isinstance(x, int):
            s = ADDON.getLocalizedString(x)
            return s if s else (fallback if fallback else str(x))
        return str(x)
    except Exception:
        return fallback if fallback else str(x)

def profile_path(rel=""):
    base = xbmcvfs.translatePath("special://profile/")
    return os.path.join(base, rel) if rel else base

def get_str(key, default=""):
    v = ADDON.getSetting(key)
    return v if v != "" else default

def get_bool(key, default=False):
    v = ADDON.getSetting(key)
    if v == "":
        return default
    return v.lower() in ("true", "1", "yes", "on")

def set_bool(key, value):
    ADDON.setSetting(key, "true" if bool(value) else "false")

# ----- core -----
def write_advancedsettings():
    host = get_str("db_host", "")
    port = get_str("db_port", "3306")
    user = get_str("db_user", "")
    pw   = get_str("db_pass", "")
    vbase= get_str("videos_base", "MyVideos")
    mbase= get_str("music_base", "MyMusic")
    iw   = "true" if get_bool("import_watched", True) else "false"
    ir   = "true" if get_bool("import_resume",  True) else "false"

    if not host or not user:
        return False, "Missing host/user"

    xml = f"""<advancedsettings>
  <videodatabase>
    <type>mysql</type>
    <host>{host}</host>
    <port>{port}</port>
    <user>{user}</user>
    <pass>{pw}</pass>
    <name>{vbase}</name>
  </videodatabase>
  <musicdatabase>
    <type>mysql</type>
    <host>{host}</host>
    <port>{port}</port>
    <user>{user}</user>
    <pass>{pw}</pass>
    <name>{mbase}</name>
  </musicdatabase>
  <videolibrary>
    <importwatchedstate>{iw}</importwatchedstate>
    <importresumepoint>{ir}</importresumepoint>
  </videolibrary>
</advancedsettings>
""".rstrip() + "\n"

    # zajisti existenci profilu
    prof_dir = profile_path()
    xbmcvfs.mkdirs(prof_dir)

    target = profile_path("advancedsettings.xml")
    try:
        f = xbmcvfs.File(target, 'w')
        try:
            f.write(xml)
        finally:
            f.close()
        return True, target
    except Exception as e:
        return False, str(e)

def wizard():
    # První spuštění – nabídni otevření nastavení
    if not get_bool("first_run_done", False):
        if xbmcgui.Dialog().yesno(ls(30000, "Libretto nastavení"),
                                  ls(30001, "Otevřít nastavení pro připojení k MariaDB a exportovat advancedsettings.xml?")):
            ADDON.openSettings()
        set_bool("first_run_done", True)

    # Automatický export, pokud máme údaje a ještě nebyl proveden
    host = get_str("db_host", "")
    user = get_str("db_user", "")
    if host and user and not get_bool("export_done", False):
        ok, msg = write_advancedsettings()
        if ok:
            set_bool("export_done", True)
            xbmcgui.Dialog().notification("Libretto", ls(30003, "Nastavení uloženo."), xbmcgui.NOTIFICATION_INFO, 4000)
        else:
            xbmcgui.Dialog().notification("Libretto", ls(30004, "Chyba exportu") + ": " + str(msg),
                                          xbmcgui.NOTIFICATION_ERROR, 5000)

if __name__ == "__main__":
    # Service start → spusť wizard
    try:
        wizard()
    except Exception as e:
        xbmcgui.Dialog().notification("Libretto", f"Chyba: {e}", xbmcgui.NOTIFICATION_ERROR, 6000)