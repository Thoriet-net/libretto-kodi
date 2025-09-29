
# -*- coding: utf-8 -*-
import sys, json, urllib.parse, os
import xbmc  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore
import xbmcaddon  # type: ignore
import xbmcvfs  # type: ignore

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]

def log(msg):
    try:
        xbmc.log(f"[NextSmart] {msg}", xbmc.LOGINFO)
    except Exception:
        pass

def build_url(params):
    return BASE_URL + "?" + urllib.parse.urlencode(params, doseq=True)

def rpc(method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    resp = xbmc.executeJSONRPC(json.dumps(body))
    data = json.loads(resp)
    if "error" in data:
        raise RuntimeError(f"JSON-RPC error: {data['error']}")
    return data.get("result", {})

# ---------------- Library helpers ----------------


def get_inprogress_by_show():
    """Return {tvshowid(str): episode_dict} for most-recent in-progress episodes."""
    props = [
        "title","season","episode","showtitle","tvshowid",
        "playcount","file","runtime","art","dateadded","lastplayed","resume"
    ]
    r = rpc("VideoLibrary.GetEpisodes", {
        "properties": props,
        "filter": {"field": "inprogress", "operator": "true", "value": ""}
    })
    eps = r.get("episodes", []) or []
    # Sort by lastplayed desc (string compare works for YYYY-MM-DD HH:MM:SS)
    eps.sort(key=lambda e: e.get("lastplayed",""), reverse=True)
    mapping = {}
    for ep in eps:
        tvsid = str(ep.get("tvshowid"))
        if tvsid not in mapping:
            mapping[tvsid] = ep
    log(f"inprogress shows: {len(mapping)}")
    return mapping


def get_started_show_ids():
    """Return set of tvshowids (as str) for shows with any watched episode."""
    r = rpc("VideoLibrary.GetEpisodes", {
        "properties": ["tvshowid","playcount","lastplayed"],
        "filter": {"field": "playcount", "operator": "greaterthan", "value": "0"}
    })
    ids = {str(ep.get("tvshowid")) for ep in (r.get("episodes", []) or [])}
    log(f"started shows via watched eps: {len(ids)}")
    return ids


def get_first_unplayed_episode(tvshow_id, skip_specials=True):
    """Return first unplayed episode for tvshow_id or None."""
    filt = {"and": [
        {"field": "playcount", "operator": "is", "value": "0"}
    ]}
    r = rpc("VideoLibrary.GetEpisodes", {
        "tvshowid": int(tvshow_id),
        "properties": [
            "title","season","episode","showtitle","tvshowid",
            "file","runtime","art","dateadded","lastplayed","resume"
        ],
        "filter": filt
    })
    eps = r.get("episodes", []) or []
    if skip_specials:
        eps = [e for e in eps if int(e.get("season", 0)) > 0]
    # Sort by (season, episode)
    eps.sort(key=lambda e: (int(e.get("season", 0)), int(e.get("episode", 0))))
    return eps[0] if eps else None


# ---------------- UI helpers ----------------

def get_all_tvshows():
    """Return list of dicts: [{"tvshowid": id, "title": str}] sorted by title."""
    r = rpc("VideoLibrary.GetTVShows", {
        "properties": ["title"],
        # no server-side sort; we'll sort here
    })
    shows = r.get("tvshows", []) or []
    shows.sort(key=lambda s: (s.get("title") or "").lower())
    return [{"tvshowid": int(s.get("tvshowid")), "title": s.get("title","")} for s in shows]

def pick_shows_multiselect(current_ids=None):
    """Return list of selected tvshowids (ints)."""
    current_ids = set(current_ids or [])
    shows = get_all_tvshows()
    if not shows:
        xbmcgui.Dialog().ok(ADDON_NAME, "No TV shows found in your library.")
        return []
    labels = [s["title"] for s in shows]
    # Preselect indices based on current_ids
    pre = [i for i,s in enumerate(shows) if s["tvshowid"] in current_ids]
    sel = None
    try:
        sel = xbmcgui.Dialog().multiselect("Choose TV Shows", labels, preselect=pre)
    except TypeError:
        # Older dialog signature fallback
        sel = xbmcgui.Dialog().multiselect("Choose TV Shows", labels)
    if sel is None:
        return list(current_ids)  # user cancelled → keep previous
    return [shows[i]["tvshowid"] for i in sel]


def add_dir(label, url, is_folder=True, icon=None):
    li = xbmcgui.ListItem(label=str(label))
    if icon:
        li.setArt({"icon": icon, "thumb": icon})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=is_folder)


def list_profiles():
    add_dir("Add New Smart List", build_url({"action":"add_profile"}), is_folder=False)
    profiles = load_profiles()
    for key, cfg in profiles.items():
        mode = "In-Progress only" if cfg.get("inprogress_only") else "Next-Up + In-Progress"
        add_dir(f"{cfg.get('name','List')} • {mode}", build_url({"action":"browse","profile":key}), is_folder=True)
        add_dir(f"Edit: {cfg.get('name','List')}", build_url({"action":"edit_profile","profile":key}), is_folder=False)
    add_dir("Widget Path Helper", build_url({"action":"widget_path"}), is_folder=False)
    add_dir("Delete All Lists", build_url({"action":"wipe"}), is_folder=False)
    xbmcplugin.endOfDirectory(HANDLE)


def widget_path_helper():
    profs = load_profiles()
    if not profs:
        xbmcgui.Dialog().ok(ADDON_NAME, "Create a list first.\nThen return here to copy the widget path.")
        return
    lines = []
    for key, cfg in profs.items():
        lines.append(f"[{cfg.get('name','List')}] plugin://{ADDON_ID}/?action=browse&profile={key}")
    xbmcgui.Dialog().textviewer("Widget Paths", "\n\n".join(lines))


def fs_profile_dir():
    p = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    if not xbmcvfs.exists(p):
        xbmcvfs.mkdirs(p)
    return p

def load_profiles():
    try:
        pdir = fs_profile_dir()
        fp = os.path.join(pdir, "profiles.json")
        if not os.path.exists(fp):
            return {}
        with open(fp, "r", encoding="utf-8") as f:
            data = f.read()
        return json.loads(data) if data.strip() else {}
    except Exception as e:
        log(f"profiles.json parse error: {e}")
        return {}


def save_profiles(d):
    data_dir = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    if not xbmcvfs.exists(data_dir):
        xbmcvfs.mkdirs(data_dir)
    fp = os.path.join(data_dir, "profiles.json")
    payload = json.dumps(d, indent=2)
    with xbmcvfs.File(fp, "w") as f:
        f.write(bytearray(payload, "utf-8"))


def ask_new_profile():
    kb = xbmcgui.Dialog()

    # 1) List name
    name = kb.input("List name", type=xbmcgui.INPUT_ALPHANUM, defaultt="NextSmart")
    if not name:
        return None

    # 2) Skip specials (Season 0)
    skip_specials = kb.yesno(
        "Special Episodes (Season 0)",
        "Some shows have bonus episodes, trailers, or behind-the-scenes content stored in Season 0.\n\n"
        "Do you want to skip these and only include regular episodes in this list?",
        yeslabel="Skip Specials",
        nolabel="Include Specials"
    )

    # 3) Episode selection (In-progress vs Next-Up)
    inprogress_only = kb.yesno(
        "Episode Selection",
        "Choose what type of episodes should be shown in this smart list:\n\n"
        "- Only In-Progress = episodes you have already started but not finished.\n"
        "- Allow Next-Up = also include the next unwatched episode for each show.",
        yeslabel="Only In-Progress",
        nolabel="Allow Next-Up"
    )

    # 4) Sorting (recent vs none)
    order_by_recent = kb.yesno(
        "Sorting Mode",
        "How should the list be ordered?\n\n"
        "- Recent First = newest activity or added items appear at the top.\n"
        "- No Sorting = leave the list in Kodi's natural order.",
        yeslabel="Recent First",
        nolabel="No Sorting"
    )

    # 5) Filter mode (limit to specific shows)
    choice = kb.select(
        "Limit to specific shows?",
        ["All shows (default)", "Include only selected shows", "Exclude selected shows"]
    )
    filter_mode = "all"
    filter_shows = []
    if choice == 1:
        filter_mode = "include"
        filter_shows = pick_shows_multiselect([])
    elif choice == 2:
        filter_mode = "exclude"
        filter_shows = pick_shows_multiselect([])

    return {
        "name": name,
        "skip_specials": bool(skip_specials),
        "inprogress_only": bool(inprogress_only),
        "order_by_recent": bool(order_by_recent),
        "filter_mode": filter_mode,
        "filter_shows": filter_shows,
    }

def slugify(s):
    s = s.strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in " _-":
            out.append("-")
    return "-".join(filter(None, "".join(out).split("-"))) or "list"

def add_profile():
    prof = ask_new_profile()
    if not prof:
        return xbmcplugin.endOfDirectory(HANDLE)
    profiles = load_profiles()
    key = slugify(prof["name"])
    base = key; i = 2
    while key in profiles:
        key = f"{base}-{i}"; i += 1
    profiles[key] = prof
    save_profiles(profiles)
    xbmcgui.Dialog().ok(ADDON_NAME, f"Created list '{prof['name']}'\n\nWidget path:\nplugin://{ADDON_ID}/?action=browse&profile={key}")
    list_profiles()

# ---------------- Browse / Build items ----------------

def browse_profile(profile_key):
    cfg = load_profiles().get(profile_key)
    if not cfg:
        xbmcgui.Dialog().notification(ADDON_NAME, "Profile not found", xbmcgui.NOTIFICATION_ERROR, 4000)
        return xbmcplugin.endOfDirectory(HANDLE)

    skip_specials = cfg.get("skip_specials", True)
    inprogress_only = cfg.get("inprogress_only", False)
    order_by_recent = cfg.get("order_by_recent", True)

    inprog_map = get_inprogress_by_show()
    started_ids = get_started_show_ids()
    tvshow_ids = set(inprog_map.keys()) | started_ids
    # Apply include/exclude filters
    mode = cfg.get("filter_mode", "all")
    chosen = set(int(x) for x in (cfg.get("filter_shows") or []))
    if mode == "include" and chosen:
        tvshow_ids = {sid for sid in tvshow_ids if int(sid) in chosen}
    elif mode == "exclude" and chosen:
        tvshow_ids = {sid for sid in tvshow_ids if int(sid) not in chosen}

    items = []
    for sid in tvshow_ids:
        ep = None
        if inprogress_only and sid in inprog_map:
            ep = inprog_map[sid]
        elif not inprogress_only:
            ep = inprog_map.get(sid) or get_first_unplayed_episode(sid, skip_specials)
        if not ep:
            continue

        # Build list item
        s = int(ep.get("season", 0)); e = int(ep.get("episode", 0))
        label = f"{ep.get('showtitle','')} - S{s:02d}E{e:02d} • {ep.get('title','')}"
        li = xbmcgui.ListItem(label=label)
        li.setProperty("IsPlayable", "true")
        li.setInfo("video", {
            "title": ep.get("title",""),
            "tvshowtitle": ep.get("showtitle",""),
            "season": s,
            "episode": e,
            "mediatype": "episode",
        })
        # --- Progress info for widgets/skins ---
        resume = ep.get("resume") or {}
        try:
            pos = int(float(resume.get("position", 0)))
            tot = int(float(resume.get("total", 0)))
        except Exception:
            pos = int(resume.get("position") or 0)
            tot = int(resume.get("total") or 0)

        if tot > 0 and pos > 0:
            try:
                # Available on Kodi 19/20; safe to ignore if missing
                li.setResumeTime(pos, tot)
            except Exception:
                pass
            # Common skin properties used for progress bars
            li.setProperty("resumetime", str(pos))
            li.setProperty("totaltime", str(tot))
            try:
                percent = int((pos / float(tot)) * 100)
            except Exception:
                percent = 0
            li.setProperty("PercentPlayed", str(percent))
            li.setProperty("progress", str(percent))
        art = ep.get("art") or {}
        li.setArt({"thumb": art.get("thumb",""), "fanart": art.get("fanart","")})

        # Prefer direct file path; else fall back to JSON-RPC play by episodeid (implicitly present)
        url = ep.get("file") or build_url({"action":"play", "episodeid": ep.get("episodeid", 0)})
        items.append((ep.get("lastplayed",""), ep.get("dateadded",""), url, li))

    # Sort
    if order_by_recent:
        items.sort(key=lambda t: (t[0], t[1]), reverse=True)
    else:
        items.sort(key=lambda t: (t[1], t[0]), reverse=True)

    for _,__, url, li in items:
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)

    xbmcplugin.setContent(HANDLE, "episodes")
    xbmcplugin.endOfDirectory(HANDLE)


def edit_profile(profile_key):
    profiles = load_profiles()
    cfg = profiles.get(profile_key)
    if not cfg:
        xbmcgui.Dialog().notification(ADDON_NAME, "Profile not found", xbmcgui.NOTIFICATION_ERROR, 3000)
        list_profiles()
        return

    kb = xbmcgui.Dialog()

    # Current values with safe fallbacks
    cur_name = cfg.get("name", "NextSmart")
    cur_skip = bool(cfg.get("skip_specials", True))
    cur_inprog = bool(cfg.get("inprogress_only", False))  # False => Allow Next-Up
    cur_recent = bool(cfg.get("order_by_recent", True))

    # 1) Rename
    name = kb.input("List name", type=xbmcgui.INPUT_ALPHANUM, defaultt=cur_name) or cur_name

    # 2) Skip specials (Season 0) — explanatory wording
    skip_specials = kb.yesno(
        "Special Episodes (Season 0)",
        "Some shows have bonus episodes, trailers, or behind-the-scenes content stored in Season 0.\n\n"
        "Do you want to skip these and only include regular episodes in this list?\n\n"
        f"Current: {'Skip Specials' if cur_skip else 'Include Specials'}",
        yeslabel="Skip Specials",
        nolabel="Include Specials"
    )

    # 3) Episode selection — In-progress vs Next-Up
    inprogress_only = kb.yesno(
        "Episode Selection",
        "Choose what type of episodes should be shown in this smart list:\n\n"
        "- Only In-Progress = episodes you have already started but not finished.\n"
        "- Allow Next-Up = also include the next unwatched episode for each show.\n\n"
        f"Current: {'Only In-Progress' if cur_inprog else 'Allow Next-Up'}",
        yeslabel="Only In-Progress",
        nolabel="Allow Next-Up"
    )

    # 4) Sorting — Recent vs none
    order_by_recent = kb.yesno(
        "Sorting Mode",
        "How should the list be ordered?\n\n"
        "- Recent First = newest activity or added items appear at the top.\n"
        "- No Sorting = leave the list in Kodi's natural order.\n\n"
        f"Current: {'Recent First' if cur_recent else 'No Sorting'}",
        yeslabel="Recent First",
        nolabel="No Sorting"
    )

    # 5) Filter mode — include/exclude specific shows
    modes = [
        "All shows (default)",
        "Include only selected shows",
        "Exclude selected shows",
    ]
    current_mode = cfg.get("filter_mode", "all")
    pre_idx = 0 if current_mode == "all" else (1 if current_mode == "include" else 2)
    choice = kb.select("Limit to specific shows?", modes, preselect=pre_idx)

    filter_mode = "all"
    filter_shows = cfg.get("filter_shows", []) or []
    if choice == 1:
        filter_mode = "include"
        filter_shows = pick_shows_multiselect(filter_shows)
    elif choice == 2:
        filter_mode = "exclude"
        filter_shows = pick_shows_multiselect(filter_shows)
    else:
        filter_mode = "all"
        filter_shows = []

    # Save back
    cfg.update({
        "name": name,
        "skip_specials": bool(skip_specials),
        "inprogress_only": bool(inprogress_only),
        "order_by_recent": bool(order_by_recent),
        "filter_mode": filter_mode,
        "filter_shows": [int(x) for x in filter_shows],
    })
    profiles[profile_key] = cfg
    save_profiles(profiles)

    xbmcgui.Dialog().ok(ADDON_NAME, f"List '{name}' updated.")
    list_profiles()

# ---------------- Router ----------------

def route(qs):
    action = qs.get("action", ["root"])[0]
    if action == "root":
        list_profiles()
    elif action == "add_profile":
        add_profile()
    elif action == "wipe":
        pdir = fs_profile_dir()
        fp = os.path.join(pdir, "profiles.json")
        try:
            if os.path.exists(fp):
                os.remove(fp)
            xbmcgui.Dialog().notification(ADDON_NAME, "All lists deleted", xbmcgui.NOTIFICATION_INFO, 2500)
        except Exception as e:
            xbmcgui.Dialog().notification(ADDON_NAME, f"Delete failed: {e}", xbmcgui.NOTIFICATION_ERROR, 3000)
        list_profiles()
    elif action == "edit_profile":
        key = qs.get("profile", [""])[0]
        edit_profile(key)
        return
    elif action == "browse":
        profile_key = qs.get("profile", [""])[0]
        browse_profile(profile_key)
    elif action == "widget_path":
        widget_path_helper()
    elif action == "play":
        eid = int(qs.get("episodeid", [0])[0])
        if eid:
            rpc("Player.Open", {"item": {"episodeid": eid}})
        else:
            xbmcgui.Dialog().notification(ADDON_NAME, "Missing episode id", xbmcgui.NOTIFICATION_ERROR, 3000)
        return
    else:
        xbmcplugin.endOfDirectory(HANDLE)

if __name__ == "__main__":
    qs = urllib.parse.parse_qs(sys.argv[2][1:])
    try:
        route(qs)
    except Exception as e:
        xbmcgui.Dialog().notification(ADDON_NAME, f"Error: {e}", xbmcgui.NOTIFICATION_ERROR, 5000)
        log(f"Exception: {e}")
        raise
