// HDR Ultimate - per-monitor HDR hotkeys for Windows 10/11.
// Build: build.cmd (uses the C# compiler that ships with Windows, no SDK needed)
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: System.Reflection.AssemblyTitle(HdrUltimate.Build.Name)]
[assembly: System.Reflection.AssemblyProduct(HdrUltimate.Build.Name)]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

namespace HdrUltimate
{
    // Each channel has its own name, settings folder and startup entry, so builds never touch each other's data.
    static class Build
    {
        public const string Version = "1.0.0";
#if DEV
        public const string Name = "HDR Ultimate Dev", Channel = "dev", RunName = "HDRUltimateDev", OldRunName = RunName;
        public const bool Dev = true, Release = false, InGameHdrColumn = true;
#elif PLAYTEST
        public const string Name = "HDR Ultimate Playtest", Channel = "playtest", RunName = "HDRUltimatePlaytest", OldRunName = RunName;
        public const bool Dev = false, Release = false, InGameHdrColumn = false;
#else
        public const string Name = "HDR Ultimate", Channel = "release", RunName = "HDRUltimate", OldRunName = "HdrToggle";
        public const bool Dev = false, Release = true, InGameHdrColumn = false;
#endif
    }

    // Dev and playtest builds keep a log in their settings folder; release builds log nothing.
    static class Log
    {
        static readonly object gate = new object();
        public static string FilePath { get { return Path.Combine(Config.Dir, "log.txt"); } }

        public static void Write(string message)
        {
            if (Build.Release) return;
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + message;
            if (Build.Dev) Console.WriteLine(line);
            lock (gate)
            {
                try
                {
                    Directory.CreateDirectory(Config.Dir);
                    if (File.Exists(FilePath) && new FileInfo(FilePath).Length > 1024 * 1024)
                        File.Copy(FilePath, FilePath + ".old", true);
                    if (File.Exists(FilePath) && new FileInfo(FilePath).Length > 1024 * 1024) File.WriteAllText(FilePath, "");
                    File.AppendAllText(FilePath, line + Environment.NewLine);
                }
                catch { }
            }
        }

        public static void Start()
        {
            if (Build.Release) return;
            // Environment.OSVersion reports 6.2 for exes without a manifest, so read the real build number.
            object build = Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuild", "unknown");
            Write("--- " + Build.Name + " " + Build.Version + " on Windows build " + build);
            foreach (var m in Hdr.GetMonitors())
                Write("Monitor: " + m.Label + " | HDR supported=" + m.HdrSupported + " on=" + m.HdrOn);
        }

        public static void Crash(Exception ex, bool canContinue)
        {
            Write("CRASH: " + ex);
            MessageBox.Show(Build.Name + " hit a problem" + (canContinue ? "" : " and has to close") +
                ".\n\nPlease send this file to the developer:\n" + FilePath, Build.Name, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            if (args.Length > 1 && args[0] == "--make-icon")
            {
                Theme.Set("dark");
                AppIcon.WriteIco(args[1]);
                return;
            }
            Log.Start();
            if (!Build.Release)
            {
                Application.ThreadException += (s, e) => Log.Crash(e.Exception, true);
                AppDomain.CurrentDomain.UnhandledException += (s, e) => Log.Crash((Exception)e.ExceptionObject, false);
            }
            if (args.Length > 0 && args[0] == "--list")
            {
                foreach (var m in Hdr.GetMonitors())
                    Console.WriteLine("{0} | HDR supported: {1} | HDR on: {2} | new API: {3}\n    {4}",
                        m.Label, m.HdrSupported, m.HdrOn, m.NewApi, m.DevicePath);
                return;
            }

            bool created;
            using (var mutex = new Mutex(true, Build.RunName + "_SingleInstance_7f3a91", out created))
            {
                if (!created)
                {
                    MessageBox.Show("HDR Ultimate is already running. Look for its icon in the system tray.", Build.Name);
                    return;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new TrayApp());
            }
        }
    }

    // ------------------------------------------------------------------ Native display config API

    [StructLayout(LayoutKind.Sequential)]
    struct LUID { public uint LowPart; public int HighPart; }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_SOURCE_INFO { public LUID adapterId; public uint id; public uint modeInfoIdx; public uint statusFlags; }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_TARGET_INFO
    {
        public LUID adapterId; public uint id; public uint modeInfoIdx; public uint outputTechnology;
        public uint rotation; public uint scaling; public uint refreshNum; public uint refreshDen;
        public uint scanLineOrdering; public int targetAvailable; public uint statusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_INFO { public PATH_SOURCE_INFO sourceInfo; public PATH_TARGET_INFO targetInfo; public uint flags; }

    [StructLayout(LayoutKind.Sequential, Size = 64)]
    struct MODE_INFO { public uint infoType; public uint id; public LUID adapterId; }

    [StructLayout(LayoutKind.Sequential)]
    struct DEVICE_INFO_HEADER { public uint type; public uint size; public LUID adapterId; public uint id; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct SOURCE_DEVICE_NAME
    {
        public DEVICE_INFO_HEADER header;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string viewGdiDeviceName;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct TARGET_DEVICE_NAME
    {
        public DEVICE_INFO_HEADER header;
        public uint flags; public uint outputTechnology;
        public ushort edidManufactureId; public ushort edidProductCodeId; public uint connectorInstance;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string monitorFriendlyDeviceName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string monitorDevicePath;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ADVANCED_COLOR_INFO { public DEVICE_INFO_HEADER header; public uint value; public uint colorEncoding; public uint bitsPerColorChannel; }

    // Windows 11 24H2+
    [StructLayout(LayoutKind.Sequential)]
    struct ADVANCED_COLOR_INFO_2 { public DEVICE_INFO_HEADER header; public uint value; public uint colorEncoding; public uint bitsPerColorChannel; public uint activeColorMode; }

    [StructLayout(LayoutKind.Sequential)]
    struct SET_STATE { public DEVICE_INFO_HEADER header; public uint value; }

    static class Native
    {
        public const uint QDC_ONLY_ACTIVE_PATHS = 2;
        public const uint GET_SOURCE_NAME = 1, GET_TARGET_NAME = 2, GET_ADVANCED_COLOR_INFO = 9,
            SET_ADVANCED_COLOR_STATE = 10, GET_ADVANCED_COLOR_INFO_2 = 15, SET_HDR_STATE = 16;

        [DllImport("user32.dll")] public static extern int GetDisplayConfigBufferSizes(uint flags, out uint numPaths, out uint numModes);
        [DllImport("user32.dll")] public static extern int QueryDisplayConfig(uint flags, ref uint numPaths, [Out] PATH_INFO[] paths, ref uint numModes, [Out] MODE_INFO[] modes, IntPtr topologyId);
        [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref SOURCE_DEVICE_NAME info);
        [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref TARGET_DEVICE_NAME info);
        [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref ADVANCED_COLOR_INFO info);
        [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref ADVANCED_COLOR_INFO_2 info);
        [DllImport("user32.dll")] public static extern int DisplayConfigSetDeviceInfo(ref SET_STATE info);

        [DllImport("user32.dll", SetLastError = true)] public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint mods, uint vk);
        [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr hWnd, int id);
        [DllImport("user32.dll")] public static extern short GetKeyState(int vk);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);

        [DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern bool QueryFullProcessImageName(IntPtr h, uint flags, StringBuilder name, ref int size);
        [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);

        [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
        [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)] public static extern int SetWindowTheme(IntPtr hwnd, string app, string idList);
        [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);
        [DllImport("gdi32.dll")] public static extern IntPtr AddFontMemResourceEx(IntPtr font, uint size, IntPtr reserved, ref uint count);

        // Works for most elevated processes too, unlike Process.MainModule.
        public static string ImagePath(int pid)
        {
            IntPtr h = OpenProcess(0x1000 /* PROCESS_QUERY_LIMITED_INFORMATION */, false, pid);
            if (h == IntPtr.Zero) return null;
            try
            {
                var sb = new StringBuilder(1024);
                int size = sb.Capacity;
                return QueryFullProcessImageName(h, 0, sb, ref size) ? sb.ToString() : null;
            }
            finally { CloseHandle(h); }
        }

        public static DEVICE_INFO_HEADER Header(uint type, Type structType, LUID adapter, uint id)
        {
            var h = new DEVICE_INFO_HEADER();
            h.type = type; h.size = (uint)Marshal.SizeOf(structType); h.adapterId = adapter; h.id = id;
            return h;
        }
    }

    // ------------------------------------------------------------------ HDR logic

    class MonitorInfo
    {
        public string Name, GdiName, DevicePath;
        public LUID Adapter; public uint TargetId, SourceId;
        public bool HdrSupported, HdrOn, NewApi;

        public string Label
        {
            get
            {
                string n = Name;
                if (!string.IsNullOrEmpty(GdiName)) n += "  (" + GdiName.Replace(@"\\.\", "") + ")";
                return n;
            }
        }
    }

    static class Hdr
    {
        public static List<MonitorInfo> GetMonitors()
        {
            var list = new List<MonitorInfo>();
            uint np, nm; PATH_INFO[] paths; MODE_INFO[] modes; int err;
            do
            {
                err = Native.GetDisplayConfigBufferSizes(Native.QDC_ONLY_ACTIVE_PATHS, out np, out nm);
                if (err != 0) return list;
                paths = new PATH_INFO[np]; modes = new MODE_INFO[nm];
                err = Native.QueryDisplayConfig(Native.QDC_ONLY_ACTIVE_PATHS, ref np, paths, ref nm, modes, IntPtr.Zero);
            } while (err == 122); // ERROR_INSUFFICIENT_BUFFER: topology changed between calls
            if (err != 0) return list;

            var seen = new HashSet<string>();
            for (int i = 0; i < np; i++)
            {
                var t = paths[i].targetInfo;
                string key = t.adapterId.LowPart + ":" + t.adapterId.HighPart + ":" + t.id;
                if (!seen.Add(key)) continue;

                var m = new MonitorInfo { Adapter = t.adapterId, TargetId = t.id, SourceId = paths[i].sourceInfo.id };

                var tn = new TARGET_DEVICE_NAME();
                tn.header = Native.Header(Native.GET_TARGET_NAME, typeof(TARGET_DEVICE_NAME), t.adapterId, t.id);
                if (Native.DisplayConfigGetDeviceInfo(ref tn) == 0)
                {
                    m.Name = tn.monitorFriendlyDeviceName;
                    m.DevicePath = tn.monitorDevicePath;
                }

                var sn = new SOURCE_DEVICE_NAME();
                sn.header = Native.Header(Native.GET_SOURCE_NAME, typeof(SOURCE_DEVICE_NAME), paths[i].sourceInfo.adapterId, paths[i].sourceInfo.id);
                if (Native.DisplayConfigGetDeviceInfo(ref sn) == 0) m.GdiName = sn.viewGdiDeviceName;

                if (string.IsNullOrEmpty(m.Name)) m.Name = "Built-in / Unknown display";
                // Adapter LUIDs change across reboots, so fall back to something stable.
                if (string.IsNullOrEmpty(m.DevicePath)) m.DevicePath = "name:" + m.Name + ":" + t.id;

                ReadState(m);
                list.Add(m);
            }
            return list.OrderBy(x => x.GdiName ?? "").ToList();
        }

        static void ReadState(MonitorInfo m)
        {
            var i2 = new ADVANCED_COLOR_INFO_2();
            i2.header = Native.Header(Native.GET_ADVANCED_COLOR_INFO_2, typeof(ADVANCED_COLOR_INFO_2), m.Adapter, m.TargetId);
            if (Native.DisplayConfigGetDeviceInfo(ref i2) == 0)
            {
                m.NewApi = true;
                m.HdrSupported = (i2.value & 0x10) != 0;                         // highDynamicRangeSupported
                m.HdrOn = (i2.value & 0x20) != 0 || i2.activeColorMode == 2;     // highDynamicRangeUserEnabled / HDR active
                return;
            }
            var i1 = new ADVANCED_COLOR_INFO();
            i1.header = Native.Header(Native.GET_ADVANCED_COLOR_INFO, typeof(ADVANCED_COLOR_INFO), m.Adapter, m.TargetId);
            if (Native.DisplayConfigGetDeviceInfo(ref i1) == 0)
            {
                m.HdrSupported = (i1.value & 0x1) != 0;   // advancedColorSupported
                m.HdrOn = (i1.value & 0x2) != 0;          // advancedColorEnabled
            }
        }

        public static bool SetHdr(MonitorInfo m, bool on)
        {
            bool ok = ApplyHdr(m, on);
            Log.Write("Set HDR " + (on ? "on" : "off") + " on " + m.Name + ": " + (ok ? "ok" : "FAILED"));
            return ok;
        }

        static bool ApplyHdr(MonitorInfo m, bool on)
        {
            var s = new SET_STATE();
            s.value = on ? 1u : 0u;
            if (m.NewApi)
            {
                s.header = Native.Header(Native.SET_HDR_STATE, typeof(SET_STATE), m.Adapter, m.TargetId);
                if (Native.DisplayConfigSetDeviceInfo(ref s) == 0) return true;
            }
            s.header = Native.Header(Native.SET_ADVANCED_COLOR_STATE, typeof(SET_STATE), m.Adapter, m.TargetId);
            return Native.DisplayConfigSetDeviceInfo(ref s) == 0;
        }
    }

    // ------------------------------------------------------------------ Config

    class Binding
    {
        public uint Mods; public Keys Key;
        public Dictionary<string, string> Monitors = new Dictionary<string, string>(); // device path -> name

        public string HotkeyText { get { return Hotkeys.Format(Mods, Key); } }
    }

    class Game
    {
        public string ExePath, Name;
        public string ConfigPath = "";  // the game's own settings file (blank = auto-detect)
        public bool Restore = true;     // turn HDR back off when the game closes
        public bool Ask = true;         // ask before turning HDR on
        public bool SkipIfOff;          // don't offer HDR when the game's own HDR option is off
        public bool OledCare = true;    // turn HDR off while tabbed out of the game
        public Dictionary<string, string> Monitors = new Dictionary<string, string>();

        public static string NameFor(string exe)
        {
            try
            {
                var v = FileVersionInfo.GetVersionInfo(exe);
                foreach (var s in new[] { v.ProductName, v.FileDescription })
                    if (!string.IsNullOrWhiteSpace(s)) return s.Trim();
            }
            catch { }
            return Path.GetFileNameWithoutExtension(exe);
        }
    }

    class Config
    {
        public List<Binding> Bindings = new List<Binding>();
        public List<Game> Games = new List<Game>();
        public bool ShowOsd = true, GamesEnabled = true, StartupAsked, OfferDefaultApplied, OledDefaultApplied, OledAsked;
        public string Theme = "system"; // system | light | dark
        public const int OledMinMinutes = 1, OledMaxMinutes = 30;
        public int OledMinutes = 5;     // minutes tabbed out before OLED care turns HDR off
        public bool OledEnabled;        // master switch, offered on first run; each game can still opt out

        static string AppData { get { return Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData); } }
        public static string Dir { get { return Path.Combine(AppData, Build.Name); } }
        static string FilePath { get { return Path.Combine(Dir, "config.txt"); } }

        public static Config Load()
        {
            var c = new Config();
            try
            {
                // Carry settings over from when the app was called HDR Toggle.
                string old = Path.Combine(AppData, "HdrToggle", "config.txt");
                if (Build.Release && !File.Exists(FilePath) && File.Exists(old))
                {
                    Directory.CreateDirectory(Dir);
                    File.Copy(old, FilePath);
                }
                if (!File.Exists(FilePath))
                {
                    // New installs already have the current defaults; the one-time upgrades below are for old configs.
                    c.OfferDefaultApplied = c.OledDefaultApplied = true;
                    return c;
                }
                foreach (var line in File.ReadAllLines(FilePath))
                {
                    var p = line.Split('\t');
                    if (p[0] == "osd" && p.Length > 1) c.ShowOsd = p[1] == "1";
                    else if (p[0] == "games" && p.Length > 1) c.GamesEnabled = p[1] == "1";
                    else if (p[0] == "theme" && p.Length > 1) c.Theme = p[1];
                    else if (p[0] == "startup_asked" && p.Length > 1) c.StartupAsked = p[1] == "1";
                    else if (p[0] == "offer_default" && p.Length > 1) c.OfferDefaultApplied = p[1] == "1";
                    else if (p[0] == "oled_default" && p.Length > 1) c.OledDefaultApplied = p[1] == "1";
                    else if (p[0] == "oled_asked" && p.Length > 1) c.OledAsked = p[1] == "1";
                    else if (p[0] == "oled_enabled" && p.Length > 1) c.OledEnabled = p[1] == "1";
                    else if (p[0] == "oled_minutes" && p.Length > 1)
                        c.OledMinutes = Math.Max(OledMinMinutes, Math.Min(OledMaxMinutes, int.Parse(p[1])));
                    else if (p[0] == "bind" && p.Length > 3)
                    {
                        var b = new Binding { Mods = uint.Parse(p[1]), Key = (Keys)int.Parse(p[2]) };
                        ReadMonitors(p, 3, b.Monitors);
                        c.Bindings.Add(b);
                    }
                    else if (p[0] == "game" && p.Length > 4) // pre-1.1 format
                    {
                        var g = new Game { Restore = p[1] == "1", ExePath = p[2], Name = p[3] };
                        ReadMonitors(p, 4, g.Monitors);
                        c.Games.Add(g);
                    }
                    else if (p[0] == "game3" && p.Length > 8)
                    {
                        var g = new Game
                        {
                            Restore = p[1] == "1", Ask = p[2] == "1", SkipIfOff = p[3] == "1", OledCare = p[4] == "1",
                            ConfigPath = p[5], ExePath = p[6], Name = p[7]
                        };
                        ReadMonitors(p, 8, g.Monitors);
                        c.Games.Add(g);
                    }
                    else if (p[0] == "game2" && p.Length > 7) // pre-OLED care format
                    {
                        var g = new Game
                        {
                            Restore = p[1] == "1", Ask = p[2] == "1", SkipIfOff = p[3] == "1",
                            ConfigPath = p[4], ExePath = p[5], Name = p[6]
                        };
                        ReadMonitors(p, 7, g.Monitors);
                        c.Games.Add(g);
                    }
                }
                // Games now offer HDR by default; applied once so later per-game choices stick.
                if (!c.OfferDefaultApplied)
                {
                    foreach (var g in c.Games) g.SkipIfOff = false;
                    c.OfferDefaultApplied = true;
                    c.Save();
                }
                // OLED care now covers every game once it's turned on; applied once, like the above.
                if (!c.OledDefaultApplied)
                {
                    foreach (var g in c.Games) g.OledCare = true;
                    c.OledDefaultApplied = true;
                    c.Save();
                }
            }
            catch (Exception ex) { MessageBox.Show("Could not read config: " + ex.Message, Build.Name); }
            return c;
        }

        static void ReadMonitors(string[] p, int start, Dictionary<string, string> into)
        {
            for (int i = start; i < p.Length; i++)
            {
                int eq = p[i].IndexOf('=');
                if (eq > 0) into[p[i].Substring(0, eq)] = p[i].Substring(eq + 1);
            }
        }

        static void WriteMonitors(StringBuilder sb, Dictionary<string, string> monitors)
        {
            foreach (var kv in monitors) sb.Append("\t" + kv.Key + "=" + kv.Value.Replace("\t", " "));
            sb.AppendLine();
        }

        public void Save()
        {
            var sb = new StringBuilder();
            sb.AppendLine("osd\t" + (ShowOsd ? "1" : "0"));
            sb.AppendLine("games\t" + (GamesEnabled ? "1" : "0"));
            sb.AppendLine("theme\t" + Theme);
            sb.AppendLine("startup_asked\t" + (StartupAsked ? "1" : "0"));
            sb.AppendLine("offer_default\t" + (OfferDefaultApplied ? "1" : "0"));
            sb.AppendLine("oled_default\t" + (OledDefaultApplied ? "1" : "0"));
            sb.AppendLine("oled_asked\t" + (OledAsked ? "1" : "0"));
            sb.AppendLine("oled_enabled\t" + (OledEnabled ? "1" : "0"));
            sb.AppendLine("oled_minutes\t" + OledMinutes);
            foreach (var b in Bindings)
            {
                sb.Append("bind\t" + b.Mods + "\t" + (int)b.Key);
                WriteMonitors(sb, b.Monitors);
            }
            foreach (var g in Games)
            {
                sb.Append("game3\t" + (g.Restore ? "1" : "0") + "\t" + (g.Ask ? "1" : "0") + "\t" + (g.SkipIfOff ? "1" : "0") +
                          "\t" + (g.OledCare ? "1" : "0") + "\t" + (g.ConfigPath ?? "") + "\t" + g.ExePath + "\t" + g.Name.Replace("\t", " "));
                WriteMonitors(sb, g.Monitors);
            }
            Directory.CreateDirectory(Dir);
            File.WriteAllText(FilePath, sb.ToString());
        }
    }

    // ------------------------------------------------------------------ Hotkeys

    static class Hotkeys
    {
        public const uint ALT = 1, CTRL = 2, SHIFT = 4, WIN = 8, NOREPEAT = 0x4000;

        public static string Format(uint mods, Keys key)
        {
            var parts = new List<string>();
            if ((mods & CTRL) != 0) parts.Add("Ctrl");
            if ((mods & ALT) != 0) parts.Add("Alt");
            if ((mods & SHIFT) != 0) parts.Add("Shift");
            if ((mods & WIN) != 0) parts.Add("Win");
            if (key != Keys.None) parts.Add(KeyName(key));
            return string.Join(" + ", parts);
        }

        static string KeyName(Keys k)
        {
            if (k >= Keys.D0 && k <= Keys.D9) return ((char)('0' + (k - Keys.D0))).ToString();
            switch (k)
            {
                case Keys.OemMinus: return "-";
                case Keys.Oemplus: return "=";
                case Keys.Oemcomma: return ",";
                case Keys.OemPeriod: return ".";
                case Keys.OemQuestion: return "/";
                case Keys.OemSemicolon: return ";";
                case Keys.OemQuotes: return "'";
                case Keys.OemOpenBrackets: return "[";
                case Keys.OemCloseBrackets: return "]";
                case Keys.OemPipe: return "\\";
                case Keys.Oemtilde: return "`";
                case Keys.Next: return "PageDown";
                case Keys.Prior: return "PageUp";
                default: return k.ToString();
            }
        }
    }

    class HotkeyWindow : NativeWindow, IDisposable
    {
        public event Action<int> Pressed;
        readonly List<int> ids = new List<int>();

        public HotkeyWindow() { CreateHandle(new CreateParams()); }

        public bool Register(int id, uint mods, Keys key)
        {
            if (!Native.RegisterHotKey(Handle, id, mods | Hotkeys.NOREPEAT, (uint)key)) return false;
            ids.Add(id);
            return true;
        }

        public void UnregisterAll()
        {
            foreach (var id in ids) Native.UnregisterHotKey(Handle, id);
            ids.Clear();
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == 0x0312 && Pressed != null) Pressed(m.WParam.ToInt32()); // WM_HOTKEY
            base.WndProc(ref m);
        }

        public void Dispose() { UnregisterAll(); DestroyHandle(); }
    }

    // Finds a game's own settings file and reads whether its in-game HDR option is on.
    static class GameConfig
    {
        static string Env(Environment.SpecialFolder f) { return Environment.GetFolderPath(f); }
        static string LocalAppData { get { return Env(Environment.SpecialFolder.LocalApplicationData); } }
        static string Documents { get { return Env(Environment.SpecialFolder.MyDocuments); } }
        static string Profile { get { return Env(Environment.SpecialFolder.UserProfile); } }

        // Games whose settings file isn't found by the generic search.
        static readonly Dictionary<string, string[]> Known = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase)
        {
            { "cyberpunk2077.exe", new[] { @"%LOCALAPPDATA%\CD Projekt Red\Cyberpunk 2077\UserSettings.json" } },
            { "rdr2.exe", new[] { @"%DOCUMENTS%\Rockstar Games\Red Dead Redemption 2\Settings\system.xml" } },
            { "gta5.exe", new[] { @"%DOCUMENTS%\Rockstar Games\GTA V\settings.xml" } },
            { "witcher3.exe", new[] { @"%DOCUMENTS%\The Witcher 3\user.settings", @"%DOCUMENTS%\The Witcher 3\dx12user.settings" } },
            { "hogwartslegacy.exe", new[] { @"%LOCALAPPDATA%\Hogwarts Legacy\Saved\Config\WindowsNoEditor\GameUserSettings.ini",
                                            @"%LOCALAPPDATA%\Hogwarts Legacy\Saved\Config\Windows\GameUserSettings.ini" } },
        };

        static readonly string[] Exts = { ".ini", ".json", ".xml", ".cfg", ".txt", ".config", ".settings", ".lua", ".yaml", ".yml" };

        public static string Find(Game g)
        {
            if (!string.IsNullOrEmpty(g.ConfigPath) && File.Exists(g.ConfigPath)) return g.ConfigPath;
            string exeFile = Path.GetFileName(g.ExePath ?? "");
            string[] known;
            if (Known.TryGetValue(exeFile, out known))
                foreach (var k in known)
                {
                    string p = k.Replace("%LOCALAPPDATA%", LocalAppData).Replace("%DOCUMENTS%", Documents);
                    if (File.Exists(p)) return p;
                }

            var names = Candidates(g);
            // Unreal Engine games: %LOCALAPPDATA%\<Project>\Saved\Config\<Platform>\GameUserSettings.ini
            foreach (var n in names)
            {
                string cfgDir = Path.Combine(LocalAppData, n, "Saved", "Config");
                if (!Directory.Exists(cfgDir)) continue;
                foreach (var d in SafeDirs(cfgDir))
                {
                    string ini = Path.Combine(d, "GameUserSettings.ini");
                    if (File.Exists(ini)) return ini;
                }
            }

            // Generic: look in the usual save/config folders (optionally under a publisher folder) for a file with an HDR setting.
            var roots = new List<string> { LocalAppData, Env(Environment.SpecialFolder.ApplicationData), Documents,
                Path.Combine(Documents, "My Games"), Path.Combine(Profile, "Saved Games"), Path.Combine(Profile, "AppData", "LocalLow") };
            var dirs = new List<string>();
            foreach (var root in roots.Where(Directory.Exists))
                foreach (var n in names)
                {
                    string d = Path.Combine(root, n);
                    if (Directory.Exists(d)) dirs.Add(d);
                    foreach (var publisher in SafeDirs(root))
                    {
                        d = Path.Combine(publisher, n);
                        if (Directory.Exists(d)) dirs.Add(d);
                    }
                }
            string best = null; int bestScore = -1;
            Action<string> scan = d =>
            {
                foreach (var f in Files(d, 4))
                {
                    string text = Read(f);
                    if (text == null || Parse(text) == null) continue;
                    string fn = Path.GetFileName(f).ToLowerInvariant();
                    int score = Regex.IsMatch(fn, "graphic|video|display|setting|config|option|system|user") ? 2 : 1;
                    if (score > bestScore) { best = f; bestScore = score; }
                }
            };
            foreach (var d in dirs.Distinct(StringComparer.OrdinalIgnoreCase)) scan(d);
            // Last resort: some games keep settings next to the exe.
            if (best == null && !string.IsNullOrEmpty(g.ExePath)) scan(Path.GetDirectoryName(g.ExePath));
            return best;
        }

        // null = no settings file found or no HDR option in it
        public static bool? HdrEnabled(Game g, out string file)
        {
            file = Find(g);
            if (file == null) return null;
            string text = Read(file);
            return text == null ? null : Parse(text);
        }

        public static bool? HdrEnabledIn(string file)
        {
            string text = File.Exists(file) ? Read(file) : null;
            return text == null ? null : Parse(text);
        }

        static List<string> Candidates(Game g)
        {
            var list = new List<string>();
            Action<string> add = s =>
            {
                if (string.IsNullOrWhiteSpace(s)) return;
                s = s.Trim();
                foreach (var v in new[] { s, s.Replace(" ", ""), Regex.Replace(s, @"[^\w ]", "").Trim() })
                    if (v.Length > 1 && !list.Contains(v, StringComparer.OrdinalIgnoreCase)) list.Add(v);
            };
            add(g.Name);
            string exe = Path.GetFileNameWithoutExtension(g.ExePath ?? "");
            add(Regex.Replace(exe, @"(-Win64-Shipping|-WinGDK-Shipping|_x64|x64|_dx12|_dx11|DX12|DX11)$", "", RegexOptions.IgnoreCase));
            return list;
        }

        static IEnumerable<string> SafeDirs(string dir)
        {
            try { return Directory.GetDirectories(dir); } catch { return new string[0]; }
        }

        static IEnumerable<string> Files(string dir, int depth)
        {
            var result = new List<string>();
            var stack = new Stack<KeyValuePair<string, int>>();
            stack.Push(new KeyValuePair<string, int>(dir, 0));
            while (stack.Count > 0 && result.Count < 400)
            {
                var cur = stack.Pop();
                try
                {
                    foreach (var f in Directory.GetFiles(cur.Key))
                        if (Exts.Contains(Path.GetExtension(f).ToLowerInvariant()) && new FileInfo(f).Length < 1024 * 1024) result.Add(f);
                    if (cur.Value < depth)
                        foreach (var d in Directory.GetDirectories(cur.Key)) stack.Push(new KeyValuePair<string, int>(d, cur.Value + 1));
                }
                catch { }
            }
            return result;
        }

        static string Read(string file)
        {
            try
            {
                // Games often keep the file open; allow shared reads.
                using (var fs = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var sr = new StreamReader(fs, true))
                    return sr.ReadToEnd();
            }
            catch { return null; }
        }

        public static bool? Parse(string text)
        {
            bool? result = null;
            const RegexOptions o = RegexOptions.IgnoreCase | RegexOptions.Multiline;
            // JSON name/value pairs, e.g. Cyberpunk: { "name": "HDRModes", ..., "value": "HDR10 PQ" }
            foreach (Match m in Regex.Matches(text, "\"name\"\\s*:\\s*\"([^\"]*hdr[^\"]*)\"[^{}]*?\"value\"\\s*:\\s*\"?([^\",}\\r\\n]+)", o))
                result = Combine(result, Classify(m.Groups[1].Value, m.Groups[2].Value));
            // key = value  /  "key": value
            foreach (Match m in Regex.Matches(text, "^[ \\t]*\"?([\\w.]*hdr[\\w.]*)\"?[ \\t]*[:=][ \\t]*\"?([^\"\\r\\n,;}]+)", o))
                result = Combine(result, Classify(m.Groups[1].Value, m.Groups[2].Value));
            // XML: <hdr value="true" />  or  <HdrEnabled>true</HdrEnabled>
            foreach (Match m in Regex.Matches(text, "<([\\w.]*hdr[\\w.]*)[^>]*?value\\s*=\\s*\"([^\"]*)\"", o))
                result = Combine(result, Classify(m.Groups[1].Value, m.Groups[2].Value));
            foreach (Match m in Regex.Matches(text, "<([\\w.]*hdr[\\w.]*)>([^<]*)</", o))
                result = Combine(result, Classify(m.Groups[1].Value, m.Groups[2].Value));
            return result;
        }

        static bool? Combine(bool? a, bool? b)
        {
            if (a == true || b == true) return true;
            if (a == false || b == false) return false;
            return null;
        }

        static bool? Classify(string key, string value)
        {
            key = key.ToLowerInvariant();
            value = value.Trim().ToLowerInvariant();
            // Calibration values (HDRBrightness, HDRPaperWhite, HDRMaxNits...) aren't the on/off switch.
            if (Regex.IsMatch(key, "bright|nits|white|lum|gamma|contrast|calib|peak|black|saturat|tonemap|exposure|paper|ui|hud")) return null;
            switch (value)
            {
                case "true": case "on": case "enabled": case "enable": case "yes": return true;
                case "false": case "off": case "disabled": case "disable": case "no": case "none": case "sdr": return false;
            }
            if (value.Contains("hdr")) return true; // e.g. "HDR10 PQ", "HDR10 scRGB"
            int n;
            if (int.TryParse(value, out n) && Regex.IsMatch(key, "enable|use|output|mode|on$|toggle|active|^b?hdr$|hdr$"))
                return n != 0;
            return null;
        }
    }

    static class GameWatcher
    {
        public static List<Game> Running(List<Game> games)
        {
            var found = new HashSet<Game>();
            if (games.Count == 0) return found.ToList();
            var byName = games.GroupBy(g => Path.GetFileNameWithoutExtension(g.ExePath), StringComparer.OrdinalIgnoreCase)
                              .ToDictionary(x => x.Key, x => x.ToList(), StringComparer.OrdinalIgnoreCase);
            foreach (var p in Process.GetProcesses())
            {
                using (p)
                {
                    List<Game> candidates;
                    try { if (!byName.TryGetValue(p.ProcessName, out candidates)) continue; }
                    catch { continue; }
                    // If anti-cheat hides the path, fall back to matching the exe name alone.
                    string path = Native.ImagePath(p.Id);
                    foreach (var g in candidates)
                        if (path == null || string.Equals(path, g.ExePath, StringComparison.OrdinalIgnoreCase)) found.Add(g);
                }
            }
            return found.ToList();
        }

        // The game whose window has focus, if any. Anything in the game's folder counts, because a
        // launcher exe can start the real game from a subfolder (Hogwarts Legacy does).
        public static Game Foreground(List<Game> games)
        {
            uint pid;
            IntPtr window = Native.GetForegroundWindow();
            if (window == IntPtr.Zero || Native.GetWindowThreadProcessId(window, out pid) == 0 || pid == 0) return null;
            string path = Native.ImagePath((int)pid);
            if (path != null)
                return games.FirstOrDefault(g => path.StartsWith(Path.GetDirectoryName(g.ExePath) + "\\", StringComparison.OrdinalIgnoreCase));
            // Anti-cheat can hide the path here too.
            try
            {
                using (var p = Process.GetProcessById((int)pid))
                    return games.FirstOrDefault(g => string.Equals(p.ProcessName, Path.GetFileNameWithoutExtension(g.ExePath), StringComparison.OrdinalIgnoreCase));
            }
            catch { return null; }
        }
    }

    // ------------------------------------------------------------------ Tray app

    class TrayApp : ApplicationContext
    {
        readonly NotifyIcon tray;
        readonly HotkeyWindow hotkeys;
        readonly ContextMenuStrip menu;
        readonly Config cfg;
        readonly System.Windows.Forms.Timer poll = new System.Windows.Forms.Timer { Interval = 2000 };
        SettingsForm settings;
        string lastIndicator;
        // Games currently detected, and which monitors we switched on for them (monitor path -> exe paths holding it on).
        readonly Dictionary<string, Game> runningGames = new Dictionary<string, Game>(StringComparer.OrdinalIgnoreCase);
        readonly Dictionary<string, HashSet<string>> autoOn = new Dictionary<string, HashSet<string>>();
        readonly Dictionary<string, GamePrompt> prompts = new Dictionary<string, GamePrompt>(StringComparer.OrdinalIgnoreCase);
        // OLED care: when each watched monitor's game last had focus, and the monitors it has turned off.
        readonly Dictionary<string, DateTime> lastFocused = new Dictionary<string, DateTime>();
        readonly HashSet<string> oledOff = new HashSet<string>();

        public TrayApp()
        {
            cfg = Config.Load();
            Theme.Init(cfg.Theme);
            bool found = false;
            foreach (var g in cfg.Games.Where(g => string.IsNullOrEmpty(g.ConfigPath)))
            {
                g.ConfigPath = GameConfig.Find(g) ?? "";
                found |= g.ConfigPath != "";
            }
            if (found) cfg.Save();
            hotkeys = new HotkeyWindow();
            hotkeys.Pressed += OnHotkey;

            menu = new ContextMenuStrip { Renderer = new ThemedRenderer() };
            Theme.Changed += () => { menu.Invalidate(); lastIndicator = null; UpdateIndicator(); };
            menu.Opening += (s, e) => { BuildMenu(); e.Cancel = false; };
            BuildMenu();

            tray = new NotifyIcon { Icon = AppIcon.Get(), Text = Build.Name, ContextMenuStrip = menu, Visible = true };
            tray.MouseClick += (s, e) => { if (e.Button == MouseButtons.Left) OpenSettings(); };

            // HDR can also be changed from Windows Settings or Win+Alt+B, so keep the indicator in sync.
            CheckGames();
            UpdateIndicator();
            poll.Tick += (s, e) => { CheckGames(); CheckOledCare(); UpdateIndicator(); };
            poll.Start();

            if (!cfg.StartupAsked && !Build.Dev)
            {
                using (var prompt = new StartupPrompt())
                    if (prompt.ShowDialog() == DialogResult.Yes) Startup.Set(true);
                cfg.StartupAsked = true;
                cfg.Save();
            }
            else Startup.RefreshPath();

            if (!cfg.OledAsked && !Build.Dev)
            {
                using (var prompt = new StartupPrompt("Turn on OLED care?",
                    "OLED care turns HDR off on a game's monitors after you've been tabbed out of it for a few minutes, " +
                    "and back on when you return, to help prevent burn-in. You can change this any time under Settings > OLED care.",
                    "Turn on OLED care"))
                    cfg.OledEnabled = prompt.ShowDialog() == DialogResult.Yes;
                cfg.OledAsked = true;
                cfg.Save();
            }

            var failed = RegisterAll();
            if (cfg.Bindings.Count == 0 && cfg.Games.Count == 0) OpenSettings();
            else if (failed.Count > 0)
                tray.ShowBalloonTip(5000, Build.Name, "These hotkeys are already used by another app: " + string.Join(", ", failed), ToolTipIcon.Warning);
        }

        public List<string> RegisterAll()
        {
            hotkeys.UnregisterAll();
            var failed = new List<string>();
            for (int i = 0; i < cfg.Bindings.Count; i++)
            {
                var b = cfg.Bindings[i];
                if (hotkeys.Register(i + 1, b.Mods, b.Key)) continue;
                failed.Add(b.HotkeyText);
                Log.Write("Hotkey taken by another app: " + b.HotkeyText);
            }
            return failed;
        }

        void UpdateIndicator()
        {
            var mons = Hdr.GetMonitors().Where(m => m.HdrSupported).ToList();
            string tip = mons.Count == 0 ? "HDR Ultimate - no HDR monitors"
                : string.Join("\n", mons.Select(m => m.Name + ": HDR " + (m.HdrOn ? "ON" : "off")));
            if (tip.Length > 63) tip = tip.Substring(0, 63); // NotifyIcon limit
            string key = string.Join("", mons.Select(m => m.HdrOn ? "1" : "0")) + tip;
            if (key == lastIndicator) return;
            lastIndicator = key;
            tray.Icon = AppIcon.ForState(mons.Select(m => m.HdrOn).ToList());
            tray.Text = tip;
        }

        void CheckGames()
        {
            var running = cfg.GamesEnabled ? GameWatcher.Running(cfg.Games) : new List<Game>();
            var runningPaths = new HashSet<string>(running.Select(g => g.ExePath), StringComparer.OrdinalIgnoreCase);
            foreach (var g in running.Where(g => !runningGames.ContainsKey(g.ExePath)).ToList())
            {
                runningGames[g.ExePath] = g;
                GameStarted(g);
            }
            // Also catches games removed from the list (or detection switched off) while running.
            foreach (var g in runningGames.Values.Where(g => !runningPaths.Contains(g.ExePath)).ToList())
            {
                runningGames.Remove(g.ExePath);
                GameStopped(g);
            }
        }

        void GameStarted(Game g)
        {
            Log.Write("Game started: " + g.Name + " (" + g.ExePath + ")");
            // Monitors already switched on for another running game just pick up this game as a holder too.
            foreach (var path in g.Monitors.Keys)
            {
                HashSet<string> holders;
                if (autoOn.TryGetValue(path, out holders)) holders.Add(g.ExePath);
            }
            var off = Hdr.GetMonitors().Where(m => m.HdrSupported && g.Monitors.ContainsKey(m.DevicePath) && !m.HdrOn).ToList();
            if (off.Count == 0) return; // HDR already on everywhere this game wants it

            string file;
            bool? inGame = GameConfig.HdrEnabled(g, out file);
            Log.Write("In-game HDR for " + g.Name + ": " + (inGame.HasValue ? inGame.Value.ToString() : "unknown") + " (" + (file ?? "no settings file") + ")");
            if (inGame == false && g.SkipIfOff)
            {
                Notify(g.Name + ": HDR is off in the game's settings  ·  Windows HDR left off", 7000); // needs reading, unlike a quick status
                return;
            }
            if (!g.Ask) { EnableFor(g); return; }

            var prompt = new GamePrompt(g, off.Select(m => m.Name).ToList(), inGame);
            prompt.Answered += answer =>
            {
                prompts.Remove(g.ExePath);
                if (answer == GamePrompt.Always) { g.Ask = false; cfg.Save(); }
                if (answer != GamePrompt.No && runningGames.ContainsKey(g.ExePath)) EnableFor(g);
            };
            prompts[g.ExePath] = prompt;
            prompt.Show();
        }

        void EnableFor(Game g)
        {
            var changed = new List<MonitorInfo>();
            foreach (var m in Hdr.GetMonitors().Where(m => m.HdrSupported && g.Monitors.ContainsKey(m.DevicePath)))
            {
                HashSet<string> holders;
                if (autoOn.TryGetValue(m.DevicePath, out holders)) { holders.Add(g.ExePath); continue; }
                if (m.HdrOn) continue; // already on before the game started: leave it on afterwards too
                if (Hdr.SetHdr(m, true))
                {
                    autoOn[m.DevicePath] = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { g.ExePath };
                    changed.Add(m);
                }
            }
            AnnounceHdrOn(changed, g.Name);
            UpdateIndicator();
        }

        // Waits for the display to settle, re-reads the real state, and only then shows the banner.
        void AnnounceHdrOn(List<MonitorInfo> monitors, string detail)
        {
            if (monitors.Count == 0) return;
            var paths = new HashSet<string>(monitors.Select(m => m.DevicePath));
            var settle = new System.Windows.Forms.Timer { Interval = 1500 };
            settle.Tick += (s, e) =>
            {
                settle.Dispose();
                var now = Hdr.GetMonitors().Where(m => paths.Contains(m.DevicePath)).ToList();
                foreach (var m in now.Where(m => m.HdrOn))
                    if (cfg.ShowOsd) new HdrBanner(m, detail).Show();
                var missed = now.Where(m => !m.HdrOn).Select(m => m.Name).ToList();
                if (missed.Count > 0) Notify("HDR didn't turn on  ·  " + string.Join(", ", missed));
                UpdateIndicator();
            };
            settle.Start();
        }

        void GameStopped(Game g)
        {
            Log.Write("Game stopped: " + g.Name);
            GamePrompt prompt;
            if (prompts.TryGetValue(g.ExePath, out prompt)) { prompts.Remove(g.ExePath); prompt.Dismiss(); }
            var mons = Hdr.GetMonitors();
            var changed = new List<string>();
            foreach (var path in autoOn.Keys.ToList())
            {
                var holders = autoOn[path];
                if (!holders.Remove(g.ExePath) || holders.Count > 0) continue; // another HDR game still needs it
                autoOn.Remove(path);
                if (!g.Restore) continue;
                var m = mons.FirstOrDefault(x => x.DevicePath == path);
                if (m != null && m.HdrOn && Hdr.SetHdr(m, false)) changed.Add(m.Name);
            }
            if (changed.Count > 0) Notify(g.Name + " closed  ·  HDR OFF  ·  " + string.Join(", ", changed));
            UpdateIndicator();
        }

        // Turns HDR off on a game's monitors after it's been tabbed out for a while, and back on when it regains focus.
        void CheckOledCare()
        {
            // cfg.Games, because editing a running game replaces its object.
            var running = cfg.Games.Where(g => runningGames.ContainsKey(g.ExePath)).ToList();
            var watched = new HashSet<string>(running.Where(g => cfg.OledEnabled && g.OledCare).SelectMany(g => g.Monitors.Keys));
            foreach (var path in lastFocused.Keys.Where(k => !watched.Contains(k)).ToList()) lastFocused.Remove(path);
            if (watched.Count == 0 && oledOff.Count == 0) return;

            var mons = Hdr.GetMonitors();
            var focusedGame = GameWatcher.Foreground(running);
            Func<string, bool> focused = path => focusedGame != null && focusedGame.Monitors.ContainsKey(path);

            var restored = new List<MonitorInfo>();
            foreach (var path in oledOff.ToList())
            {
                if (running.Any(g => g.Monitors.ContainsKey(path)) && watched.Contains(path) && !focused(path)) continue;
                oledOff.Remove(path);
                if (!running.Any(g => g.Monitors.ContainsKey(path))) continue; // game closed: nothing to come back to
                var m = mons.FirstOrDefault(x => x.DevicePath == path);
                if (m != null && !m.HdrOn && Hdr.SetHdr(m, true)) restored.Add(m);
            }
            if (restored.Count > 0) Log.Write("OLED care restored HDR: " + string.Join(", ", restored.Select(m => m.Name)));
            AnnounceHdrOn(restored, focusedGame != null ? focusedGame.Name : "");

            var limit = TimeSpan.FromMinutes(cfg.OledMinutes);
            var now = DateTime.UtcNow;
            var turnedOff = new List<string>();
            foreach (var path in watched)
            {
                if (oledOff.Contains(path)) continue; // HDR switched back on by hand stays on
                var m = mons.FirstOrDefault(x => x.DevicePath == path);
                // The clock only runs while HDR is actually on, so turning it on by hand gets a full wait.
                if (focused(path) || !lastFocused.ContainsKey(path) || m == null || !m.HdrOn) { lastFocused[path] = now; continue; }
                if (now - lastFocused[path] < limit) continue;
                if (!Hdr.SetHdr(m, false)) continue;
                oledOff.Add(path);
                turnedOff.Add(m.Name);
            }
            if (turnedOff.Count > 0)
            {
                Log.Write("OLED care turned HDR off: " + string.Join(", ", turnedOff));
                Notify("OLED care  ·  HDR OFF  ·  " + string.Join(", ", turnedOff));
            }
        }

        void OnHotkey(int id)
        {
            if (id < 1 || id > cfg.Bindings.Count) return;
            var b = cfg.Bindings[id - 1];
            Log.Write("Hotkey pressed: " + b.HotkeyText);
            var targets = Hdr.GetMonitors().Where(m => b.Monitors.ContainsKey(m.DevicePath) && m.HdrSupported).ToList();
            if (targets.Count == 0) { Notify("No HDR monitor from this hotkey is connected"); return; }

            // Mixed state -> turn everything on; all on -> turn everything off.
            bool turnOn = targets.Any(m => !m.HdrOn);
            var failed = new List<string>();
            foreach (var m in targets)
                if (!Hdr.SetHdr(m, turnOn)) failed.Add(m.Name);

            if (turnOn)
            {
                AnnounceHdrOn(targets.Where(m => !failed.Contains(m.Name)).ToList(), "");
                if (failed.Count > 0) Notify("Failed: " + string.Join(", ", failed));
            }
            else
            {
                string msg = "HDR OFF  ·  " + string.Join(", ", targets.Select(m => m.Name));
                if (failed.Count > 0) msg += "\nFailed: " + string.Join(", ", failed);
                Notify(msg);
            }
            UpdateIndicator();
        }

        void ToggleOne(MonitorInfo m)
        {
            if (!Hdr.SetHdr(m, !m.HdrOn)) Notify("Could not change HDR on " + m.Name);
            else if (m.HdrOn) Notify("HDR OFF  ·  " + m.Name);
            else AnnounceHdrOn(new List<MonitorInfo> { m }, "");
            UpdateIndicator();
        }

        void Notify(string msg, int milliseconds = 1800)
        {
            if (cfg.ShowOsd) Osd.Flash(msg, milliseconds);
        }

        void BuildMenu()
        {
            menu.Items.Clear();
            foreach (var m in Hdr.GetMonitors())
            {
                var mon = m;
                var item = new ToolStripMenuItem(m.Name + (m.HdrSupported ? "" : "  — no HDR"));
                item.Checked = m.HdrOn;
                item.Enabled = m.HdrSupported;
                item.Click += (s, e) => ToggleOne(mon);
                menu.Items.Add(item);
            }
            menu.Items.Add(new ToolStripSeparator());
            foreach (var g in runningGames.Values)
                menu.Items.Add(new ToolStripMenuItem("Detected: " + g.Name) { Enabled = false });
            menu.Items.Add("Settings", null, (s, e) => OpenSettings());
            menu.Items.Add("Exit", null, (s, e) => ExitThread());
        }

        void OpenSettings()
        {
            if (settings != null) { settings.Activate(); return; }
            // Hotkeys stay live while settings are open; they're only paused while a combo is being captured.
            settings = new SettingsForm(cfg, () => { cfg.Save(); return RegisterAll(); }, () => hotkeys.UnregisterAll());
            settings.FormClosed += (s, e) => { settings = null; };
            settings.Show();
            settings.Activate();
        }

        protected override void ExitThreadCore()
        {
            poll.Dispose();
            tray.Visible = false;
            tray.Dispose();
            hotkeys.Dispose();
            base.ExitThreadCore();
        }
    }

    // ------------------------------------------------------------------ Theme

    // Orbitron is embedded in the exe, so it works without being installed.
    static class HeaderFont
    {
        static readonly PrivateFontCollection fonts = new PrivateFontCollection();
        static readonly FontFamily family;
        public static FontFamily Family { get { return family; } }
        public static readonly Font Column, Body;

        static HeaderFont()
        {
            try
            {
                using (var s = typeof(HeaderFont).Assembly.GetManifestResourceStream("Orbitron.ttf"))
                {
                    var data = new byte[s.Length];
                    s.Read(data, 0, data.Length);
                    IntPtr mem = Marshal.AllocCoTaskMem(data.Length); // stays allocated for the life of the app
                    Marshal.Copy(data, 0, mem, data.Length);
                    fonts.AddMemoryFont(mem, data.Length);
                    uint count = 0;
                    Native.AddFontMemResourceEx(mem, (uint)data.Length, IntPtr.Zero, ref count); // WinForms labels draw through GDI
                    family = fonts.Families[0];
                }
            }
            catch { family = new FontFamily("Segoe UI"); }
            Column = Get(10f);
            // Windows 11's UI font stays much crisper at small sizes than Segoe UI.
            var installed = new InstalledFontCollection().Families.Select(f => f.Name).ToList();
            Body = new Font(new[] { "Segoe UI Variable Text Semibold", "Segoe UI Semibold" }.FirstOrDefault(installed.Contains) ?? "Segoe UI", 11f);
        }

        // Draws text as filled outlines with a thin stroke, which thickens Orbitron smoothly.
        // (Only its regular weight is reachable through GDI+, and synthetic bold smears at this size.)
        public static void Draw(Graphics g, string text, Font font, Color color, RectangleF area, StringFormat format)
        {
            var smoothing = g.SmoothingMode;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (var path = new GraphicsPath())
            using (var brush = new SolidBrush(color))
            using (var pen = new Pen(color, 0.45f) { LineJoin = LineJoin.Round })
            {
                path.AddString(text, font.FontFamily, (int)font.Style, font.SizeInPoints * g.DpiY / 72f, area, format);
                g.FillPath(brush, path);
                g.DrawPath(pen, path);
            }
            g.SmoothingMode = smoothing;
        }

        public static Font Get(float size, FontStyle style = FontStyle.Regular, GraphicsUnit unit = GraphicsUnit.Point)
        {
            return new Font(family, size, style, unit);
        }
    }

    class Palette
    {
        public Color Bg, Surface, Border, Text, Muted, Accent, AccentHover, AccentText;
        public bool Dark;
    }

    static class Theme
    {
        public static readonly Palette Light = new Palette
        {
            Bg = Color.White, Surface = Color.FromArgb(246, 246, 248), Border = Color.FromArgb(222, 222, 228),
            Text = Color.FromArgb(24, 24, 27), Muted = Color.FromArgb(108, 108, 116),
            Accent = Color.FromArgb(220, 38, 52), AccentHover = Color.FromArgb(185, 28, 40), AccentText = Color.White
        };
        public static readonly Palette Dark = new Palette
        {
            Bg = Color.Black, Surface = Color.FromArgb(20, 20, 24), Border = Color.FromArgb(50, 50, 58),
            Text = Color.FromArgb(240, 240, 245), Muted = Color.FromArgb(150, 150, 160),
            Accent = Color.FromArgb(147, 88, 255), AccentHover = Color.FromArgb(124, 58, 237), AccentText = Color.White, Dark = true
        };

        // Logo only; everything else uses the theme's accent.
        public static LinearGradientBrush Rainbow(RectangleF area, float angle = 0f)
        {
            var brush = new LinearGradientBrush(area, Color.Red, Color.Violet, angle);
            brush.InterpolationColors = new ColorBlend
            {
                Colors = new[]
                {
                    Color.FromArgb(255, 59, 48), Color.FromArgb(255, 149, 0), Color.FromArgb(255, 204, 0), Color.FromArgb(52, 199, 89),
                    Color.FromArgb(0, 122, 255), Color.FromArgb(88, 86, 214), Color.FromArgb(175, 82, 222)
                },
                Positions = new[] { 0f, 1 / 6f, 2 / 6f, 3 / 6f, 4 / 6f, 5 / 6f, 1f }
            };
            return brush;
        }

        public static string Mode = "system";
        public static Palette Current = Light;
        public static event Action Changed;

        public static void Init(string mode)
        {
            Mode = mode;
            Current = Resolve();
            SystemEvents.UserPreferenceChanged += (s, e) => { if (Mode == "system") Set("system"); };
        }

        public static void Set(string mode)
        {
            Mode = mode;
            var p = Resolve();
            if (p == Current) return;
            Current = p;
            if (Changed != null) Changed();
        }

        static Palette Resolve()
        {
            if (Mode == "light") return Light;
            if (Mode == "dark") return Dark;
            try
            {
                using (var k = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))
                {
                    object v = k == null ? null : k.GetValue("AppsUseLightTheme");
                    return v is int && (int)v == 0 ? Dark : Light;
                }
            }
            catch { return Light; }
        }

        public static Color Blend(Color a, Color b, double t)
        {
            return Color.FromArgb((int)(a.R + (b.R - a.R) * t), (int)(a.G + (b.G - a.G) * t), (int)(a.B + (b.B - a.B) * t));
        }

        static int ColorRef(Color c) { return c.R | (c.G << 8) | (c.B << 16); }

        static void Dwm(IntPtr h, int attr, int value) { Native.DwmSetWindowAttribute(h, attr, ref value, 4); }

        public static void ApplyTitleBar(Form f)
        {
            if (!f.IsHandleCreated) return;
            var p = Current;
            Dwm(f.Handle, 20, p.Dark ? 1 : 0);        // DWMWA_USE_IMMERSIVE_DARK_MODE
            Dwm(f.Handle, 35, ColorRef(p.Bg));        // caption colour (Windows 11)
            Dwm(f.Handle, 34, ColorRef(p.Border));    // border colour
            Dwm(f.Handle, 36, ColorRef(p.Text));      // caption text colour
            // Separate exact-size icons; Form.Icon would shrink one bitmap for the title bar.
            Native.SendMessage(f.Handle, 0x80, (IntPtr)0, AppIcon.Get(SystemInformation.SmallIconSize.Width).Handle); // WM_SETICON small
            Native.SendMessage(f.Handle, 0x80, (IntPtr)1, AppIcon.Get(SystemInformation.IconSize.Width).Handle);      // WM_SETICON big
        }

        static void DarkScrollbars(Control c)
        {
            Native.SetWindowTheme(c.Handle, Current.Dark ? "DarkMode_Explorer" : "Explorer", null);
        }

        public static void Apply(Control c)
        {
            var p = Current;
            string tag = c.Tag as string;
            if (c is Form) { c.BackColor = p.Bg; c.ForeColor = p.Text; }
            else if (c is Button) StyleButton((Button)c, tag == "primary");
            else if (c is TextBox) { c.BackColor = p.Surface; c.ForeColor = p.Text; }
            else if (c is Panel && tag == "field") c.BackColor = p.Surface;
            else if (c is ListView) StyleList((ListView)c);
            else if (c is Tabs) ((Tabs)c).Restyle();
            else if (c is DropdownBase || c is Toggle || c is Slider || c is LogoMark) c.Invalidate();
            else if (c is Label && tag == "muted") c.ForeColor = p.Muted;

            foreach (Control child in c.Controls) Apply(child);
        }

        static void StyleButton(Button b, bool primary)
        {
            var p = Current;
            b.FlatStyle = FlatStyle.Flat;
            b.UseVisualStyleBackColor = false;
            b.MinimumSize = new Size(b.MinimumSize.Width, 30);
            b.BackColor = primary ? p.Accent : p.Surface;
            b.ForeColor = primary ? p.AccentText : p.Text;
            b.FlatAppearance.BorderSize = 0;
            b.FlatAppearance.MouseOverBackColor = primary ? p.AccentHover : Blend(p.Surface, p.Accent, 0.15);
            b.FlatAppearance.MouseDownBackColor = primary ? p.AccentHover : Blend(p.Surface, p.Accent, 0.3);
        }

        // Borderless text box on a filled panel, matching the dropdowns.
        public static Panel Field(TextBox box, int width)
        {
            var panel = new Panel { Tag = "field", Size = new Size(width, 32) };
            box.BorderStyle = BorderStyle.None;
            box.Location = new Point(10, 6);
            box.Width = width - 20;
            panel.Controls.Add(box);
            box.GotFocus += (s, e) => panel.Invalidate();
            box.LostFocus += (s, e) => panel.Invalidate();
            panel.Paint += (s, e) =>
            {
                if (!box.Focused) return;
                using (var b = new SolidBrush(Current.Accent)) e.Graphics.FillRectangle(b, 0, panel.Height - 2, panel.Width, 2);
            };
            return panel;
        }

        static void StyleList(ListView lv)
        {
            var p = Current;
            lv.BackColor = p.Bg;
            lv.ForeColor = p.Text;
            lv.BorderStyle = BorderStyle.None;
            DarkScrollbars(lv);
            if (lv.OwnerDraw) { lv.Invalidate(); return; }

            lv.OwnerDraw = true;
            lv.SmallImageList = new ImageList { ImageSize = new Size(1, 34) }; // sets the row height
            lv.DrawColumnHeader += (s, e) =>
            {
                var c = Current;
                using (var b = new SolidBrush(c.Bg)) e.Graphics.FillRectangle(b, e.Bounds);
                using (var sf = new StringFormat { LineAlignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap })
                    HeaderFont.Draw(e.Graphics, e.Header.Text, HeaderFont.Column, c.Text, Rectangle.Inflate(e.Bounds, -6, 0), sf);
                using (var pen = new Pen(c.Accent)) e.Graphics.DrawLine(pen, e.Bounds.Left, e.Bounds.Bottom - 1, e.Bounds.Right, e.Bounds.Bottom - 1);
            };
            lv.DrawItem += (s, e) => { e.DrawDefault = false; };
            lv.DrawSubItem += (s, e) =>
            {
                var c = Current;
                bool sel = e.Item.Selected;
                using (var b = new SolidBrush(sel ? Blend(c.Bg, c.Accent, 0.16) : c.Bg)) e.Graphics.FillRectangle(b, e.Bounds);
                if (sel && e.ColumnIndex == 0)
                    using (var bar = new SolidBrush(c.Accent)) e.Graphics.FillRectangle(bar, e.Bounds.Left, e.Bounds.Top, 2, e.Bounds.Height);
                using (var pen = new Pen(c.Border)) e.Graphics.DrawLine(pen, e.Bounds.Left, e.Bounds.Bottom - 1, e.Bounds.Right, e.Bounds.Bottom - 1);
                TextRenderer.DrawText(e.Graphics, e.SubItem.Text, lv.Font, Rectangle.Inflate(e.Bounds, -6, 0),
                    e.SubItem.Tag as string == "accent" ? c.Accent : e.Item.ForeColor, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
            };
            // Stretch the last column so there's no unthemed header area to its right.
            EventHandler fill = (s, e) =>
            {
                if (lv.Columns.Count == 0) return;
                int used = 0;
                for (int i = 0; i < lv.Columns.Count - 1; i++) used += lv.Columns[i].Width;
                int w = Math.Max(60, lv.ClientSize.Width - used);
                var last = lv.Columns[lv.Columns.Count - 1];
                if (last.Width != w) last.Width = w;
            };
            lv.Resize += fill;
            lv.Layout += (s, e) => fill(s, e);
            lv.VisibleChanged += fill;
            lv.HandleCreated += fill;
            fill(lv, EventArgs.Empty); // the handle usually exists by now, so HandleCreated won't fire
        }
    }

    class ThemedForm : Form
    {
        protected override void OnHandleCreated(EventArgs e) { base.OnHandleCreated(e); Theme.ApplyTitleBar(this); }

        protected override void OnLoad(EventArgs e)
        {
            Theme.Apply(this);
            Theme.Changed += Restyle;
            base.OnLoad(e);
        }

        void Restyle() { Icon = AppIcon.Get(); Theme.Apply(this); Theme.ApplyTitleBar(this); Invalidate(true); }

        protected override void OnFormClosed(FormClosedEventArgs e) { Theme.Changed -= Restyle; base.OnFormClosed(e); }
    }

    class ThemedColors : ProfessionalColorTable
    {
        static Palette P { get { return Theme.Current; } }
        Color Hover { get { return Theme.Blend(P.Bg, P.Accent, 0.22); } }
        public override Color ToolStripDropDownBackground { get { return P.Bg; } }
        public override Color ImageMarginGradientBegin { get { return P.Bg; } }
        public override Color ImageMarginGradientMiddle { get { return P.Bg; } }
        public override Color ImageMarginGradientEnd { get { return P.Bg; } }
        public override Color MenuBorder { get { return P.Border; } }
        public override Color MenuItemBorder { get { return Hover; } }
        public override Color MenuItemSelected { get { return Hover; } }
        public override Color MenuItemSelectedGradientBegin { get { return Hover; } }
        public override Color MenuItemSelectedGradientEnd { get { return Hover; } }
        public override Color SeparatorDark { get { return P.Border; } }
        public override Color SeparatorLight { get { return P.Border; } }
    }

    class ThemedRenderer : ToolStripProfessionalRenderer
    {
        public ThemedRenderer() : base(new ThemedColors()) { RoundedEdges = false; }

        protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e)
        {
            var item = e.Item as ToolStripMenuItem;
            e.TextColor = !e.Item.Enabled ? Theme.Current.Muted : item != null && item.Checked ? Theme.Current.Accent : Theme.Current.Text;
            base.OnRenderItemText(e);
        }

        protected override void OnRenderItemCheck(ToolStripItemImageRenderEventArgs e)
        {
            var r = e.ImageRectangle;
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (var pen = new Pen(Theme.Current.Accent, 2f))
                g.DrawLines(pen, new[] { new PointF(r.X + r.Width * 0.22f, r.Y + r.Height * 0.52f),
                                         new PointF(r.X + r.Width * 0.42f, r.Y + r.Height * 0.72f),
                                         new PointF(r.X + r.Width * 0.78f, r.Y + r.Height * 0.30f) });
        }
    }

    // Orbitron label drawn with smooth antialiasing; the stock Label's GDI text looks rough in this font.
    class HeaderLabel : Label
    {
        public bool Underline;

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            using (var sf = new StringFormat(StringFormat.GenericTypographic) { LineAlignment = StringAlignment.Center })
            {
                sf.FormatFlags |= StringFormatFlags.NoWrap;
                if (AutoEllipsis) sf.Trimming = StringTrimming.EllipsisCharacter;
                HeaderFont.Draw(g, Text, Font, ForeColor, new RectangleF(Padding.Left, Padding.Top, Width - Padding.Horizontal, Height - Padding.Vertical), sf);
            }
            if (Underline)
                using (var b = new SolidBrush(Theme.Current.Accent)) g.FillRectangle(b, 10, Height - 2, Width - 20, 2);
        }
    }

    class LogoMark : Control
    {
        public LogoMark(int size)
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            Size = new Size(size, size);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Parent != null ? Parent.BackColor : Theme.Current.Bg);
            using (var bmp = AppIcon.Render(Width, AppIcon.AllOn)) e.Graphics.DrawImageUnscaled(bmp, 0, 0);
        }
    }

    // Flat tab strip (the stock TabControl can't be recoloured).
    class Tabs : Panel
    {
        readonly FlowLayoutPanel header = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 46, Padding = new Padding(8, 0, 0, 0), WrapContents = false };
        readonly Panel body = new Panel { Dock = DockStyle.Fill };
        readonly List<HeaderLabel> heads = new List<HeaderLabel>();
        readonly List<Control> pages = new List<Control>();
        int selected = -1;

        public Tabs()
        {
            Controls.Add(body);
            Controls.Add(header);
        }

        public void AddPage(string title, Control page)
        {
            int idx = heads.Count;
            var h = new HeaderLabel { Text = title, Font = HeaderFont.Get(11.5f), AutoSize = true, Padding = new Padding(10, 12, 10, 9), Margin = Padding.Empty, Cursor = Cursors.Hand };
            h.Click += (s, e) => SelectPage(idx);
            heads.Add(h);
            header.Controls.Add(h);
            page.Dock = DockStyle.Fill;
            page.Visible = false;
            pages.Add(page);
            body.Controls.Add(page);
            if (selected < 0) SelectPage(0);
        }

        public void SelectPage(int i)
        {
            selected = i;
            for (int k = 0; k < pages.Count; k++) pages[k].Visible = k == i;
            Restyle();
        }

        public void Restyle()
        {
            for (int k = 0; k < heads.Count; k++)
            {
                heads[k].ForeColor = k == selected ? Theme.Current.Text : Theme.Current.Muted;
                heads[k].Underline = k == selected;
                heads[k].Invalidate();
            }
            header.Invalidate();
        }
    }

    // Flat dropdown button; its list is a themed popup menu (a stock ComboBox can't be fully recoloured).
    abstract class DropdownBase : Control
    {
        bool open, hover;

        protected DropdownBase()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw | ControlStyles.Selectable, true);
            Size = new Size(262, 32);
            Cursor = Cursors.Hand;
            TabStop = true;
        }

        protected abstract string Summary { get; }
        protected abstract void FillMenu(ContextMenuStrip menu);

        public ContextMenuStrip BuildMenu()
        {
            var menu = new ContextMenuStrip { Renderer = new ThemedRenderer(), Font = Font, MinimumSize = new Size(Width, 0) };
            FillMenu(menu);
            foreach (ToolStripItem item in menu.Items) item.Padding = new Padding(0, 4, 0, 4);
            return menu;
        }

        void Open()
        {
            var menu = BuildMenu();
            menu.Closed += (s, e) =>
            {
                open = false;
                Invalidate();
                BeginInvoke(new Action(menu.Dispose));
            };
            open = true;
            Invalidate();
            menu.Show(this, new Point(0, Height + 2));
        }

        protected override void OnClick(EventArgs e) { base.OnClick(e); Focus(); Open(); }
        protected override bool IsInputKey(Keys k) { return k == Keys.Down || base.IsInputKey(k); }
        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (e.KeyCode == Keys.Space || e.KeyCode == Keys.Down) { e.Handled = true; Open(); }
        }
        protected override void OnGotFocus(EventArgs e) { base.OnGotFocus(e); Invalidate(); }
        protected override void OnLostFocus(EventArgs e) { base.OnLostFocus(e); Invalidate(); }
        protected override void OnMouseEnter(EventArgs e) { base.OnMouseEnter(e); hover = true; Invalidate(); }
        protected override void OnMouseLeave(EventArgs e) { base.OnMouseLeave(e); hover = false; Invalidate(); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var p = Theme.Current;
            var g = e.Graphics;
            bool lit = open || Focused;
            using (var b = new SolidBrush(hover || open ? Theme.Blend(p.Surface, p.Accent, 0.1) : p.Surface)) g.FillRectangle(b, ClientRectangle);
            if (lit)
                using (var pen = new Pen(p.Accent)) g.DrawLine(pen, 0, Height - 1, Width, Height - 1);
            TextRenderer.DrawText(g, Summary, Font, new Rectangle(10, 0, Width - 36, Height), p.Text,
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix);
            int cx = Width - 16, cy = Height / 2;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (var pen = new Pen(p.Accent, 1.6f))
                g.DrawLines(pen, open
                    ? new[] { new PointF(cx - 4, cy + 2), new PointF(cx, cy - 2), new PointF(cx + 4, cy + 2) }
                    : new[] { new PointF(cx - 4, cy - 2), new PointF(cx, cy + 2), new PointF(cx + 4, cy - 2) });
        }
    }

    class SelectDropdown : DropdownBase
    {
        string[] options = new string[0];
        int selected;
        public event Action Changed;

        public string[] Options { get { return options; } set { options = value; Invalidate(); } }
        public int Selected { get { return selected; } set { selected = value; Invalidate(); } }

        protected override string Summary { get { return selected < options.Length ? options[selected] : ""; } }

        protected override void FillMenu(ContextMenuStrip menu)
        {
            for (int i = 0; i < options.Length; i++)
            {
                int index = i;
                var item = new ToolStripMenuItem(options[i]) { Checked = i == selected };
                item.Click += (s, e) =>
                {
                    Selected = index;
                    if (Changed != null) Changed();
                };
                menu.Items.Add(item);
            }
        }
    }

    // Multi-select monitor picker: stays open while boxes are ticked.
    class MonitorDropdown : DropdownBase
    {
        readonly List<MonitorEntry> entries = new List<MonitorEntry>();
        readonly HashSet<MonitorEntry> ticked = new HashSet<MonitorEntry>();

        public void Add(MonitorEntry en, bool check)
        {
            entries.Add(en);
            if (check) ticked.Add(en);
        }

        public List<MonitorEntry> Checked { get { return entries.Where(ticked.Contains).ToList(); } }

        protected override string Summary
        {
            get { return ticked.Count == 0 ? "Choose monitors" : string.Join(", ", Checked.Select(en => en.Name)); }
        }

        protected override void FillMenu(ContextMenuStrip menu)
        {
            foreach (var en in entries)
            {
                var entry = en;
                var item = new ToolStripMenuItem(entry.Label) { CheckOnClick = true, Checked = ticked.Contains(entry) };
                item.CheckedChanged += (s, e) =>
                {
                    if (item.Checked) ticked.Add(entry); else ticked.Remove(entry);
                    Invalidate();
                };
                menu.Items.Add(item);
            }
            menu.Closing += (s, e) => { if (e.CloseReason == ToolStripDropDownCloseReason.ItemClicked) e.Cancel = true; };
        }
    }

    class Toggle : Control
    {
        bool on;
        public event Action<bool> Changed;

        public Toggle(bool value)
        {
            on = value;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.SupportsTransparentBackColor | ControlStyles.Selectable, true);
            Size = new Size(38, 20);
            Cursor = Cursors.Hand;
            TabStop = true;
        }

        void Flip()
        {
            on = !on;
            Invalidate();
            if (Changed != null) Changed(on);
        }

        protected override void OnClick(EventArgs e) { base.OnClick(e); Focus(); Flip(); }
        protected override void OnKeyDown(KeyEventArgs e) { base.OnKeyDown(e); if (e.KeyCode == Keys.Space) Flip(); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var p = Theme.Current;
            var g = e.Graphics;
            g.Clear(Parent != null ? Parent.BackColor : p.Bg);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            int d = Height - 1;
            using (var track = new GraphicsPath())
            {
                track.AddArc(0, 0, d, d, 90, 180);
                track.AddArc(Width - d - 1, 0, d, d, 270, 180);
                track.CloseFigure();
                using (var b = new SolidBrush(on ? p.Accent : p.Border)) g.FillPath(b, track);
            }
            int knob = d - 6;
            using (var b = new SolidBrush(on ? p.AccentText : p.Muted))
                g.FillEllipse(b, on ? Width - knob - 4 : 3, 3, knob, knob);
        }
    }

    // Accent track with a round knob; the current value is drawn to its right.
    class Slider : Control
    {
        const int Knob = 16, TextWidth = 64;
        readonly int min, max;
        readonly Func<int, string> format;
        int value;
        public event Action<int> Changed;

        public Slider(int min, int max, int value, Func<int, string> format)
        {
            this.min = min;
            this.max = max;
            this.value = value;
            this.format = format;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.SupportsTransparentBackColor | ControlStyles.Selectable, true);
            Size = new Size(280, 24);
            Cursor = Cursors.Hand;
            TabStop = true;
        }

        int Track { get { return Width - TextWidth - Knob; } }

        void Set(int v)
        {
            v = Math.Max(min, Math.Min(max, v));
            if (v == value) return;
            value = v;
            Invalidate();
            if (Changed != null) Changed(v);
        }

        void SetFromMouse(int x)
        {
            Set(min + (int)Math.Round((x - Knob / 2.0) / Track * (max - min)));
        }

        protected override void OnMouseDown(MouseEventArgs e) { base.OnMouseDown(e); Focus(); SetFromMouse(e.X); }
        protected override void OnMouseMove(MouseEventArgs e) { base.OnMouseMove(e); if (e.Button == MouseButtons.Left) SetFromMouse(e.X); }
        protected override bool IsInputKey(Keys key) { return key == Keys.Left || key == Keys.Right || base.IsInputKey(key); }
        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (e.KeyCode == Keys.Left) Set(value - 1);
            else if (e.KeyCode == Keys.Right) Set(value + 1);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var p = Theme.Current;
            var g = e.Graphics;
            g.Clear(Parent != null ? Parent.BackColor : p.Bg);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            int x = Knob / 2 + Track * (value - min) / (max - min), y = Height / 2;
            using (var b = new SolidBrush(p.Border)) g.FillRectangle(b, Knob / 2, y - 2, Track, 4);
            using (var b = new SolidBrush(Enabled ? p.Accent : p.Muted))
            {
                g.FillRectangle(b, Knob / 2, y - 2, x - Knob / 2, 4);
                g.FillEllipse(b, x - Knob / 2, y - Knob / 2, Knob, Knob);
            }
            TextRenderer.DrawText(g, format(value), Font, new Rectangle(Width - TextWidth, 0, TextWidth, Height), Enabled ? p.Text : p.Muted,
                TextFormatFlags.Right | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
        }
    }

    static class Startup
    {
        const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        const string Name = Build.RunName, OldName = Build.OldRunName, TaskName = Build.Name;

        public static bool Enabled { get { return TaskExists || RunEntryExists; } }

        static bool RunEntryExists
        {
            get
            {
                using (var k = Registry.CurrentUser.OpenSubKey(RunKey))
                    return k != null && (k.GetValue(Name) != null || k.GetValue(OldName) != null);
            }
        }

        static bool TaskExists { get { string output; return Schtasks("/query /tn \"" + TaskName + "\"", out output) == 0; } }

        // A sign-in task skips the delay Windows puts on Run-key apps. If Windows refuses it,
        // the Run entry is used instead. Returns false in that case.
        public static bool Set(bool on)
        {
            if (!on)
            {
                if (TaskExists) { string output; Schtasks("/delete /tn \"" + TaskName + "\" /f", out output); }
                SetRunEntry(false);
                return true;
            }
            bool task = CreateTask();
            SetRunEntry(!task);
            return task;
        }

        // Runs at launch. Only updates what the user already turned on, for when the exe has moved.
        public static void RefreshPath()
        {
            if (RunEntryExists) SetRunEntry(true);
            string xml;
            if (Schtasks("/query /tn \"" + TaskName + "\" /xml", out xml) == 0 && !xml.Contains(SecurityElement.Escape(Application.ExecutablePath)))
                CreateTask();
        }

        static void SetRunEntry(bool on)
        {
            using (var k = Registry.CurrentUser.CreateSubKey(RunKey))
            {
                k.DeleteValue(OldName, false);
                if (on) k.SetValue(Name, "\"" + Application.ExecutablePath + "\"");
                else k.DeleteValue(Name, false);
            }
        }

        public static string TaskXml(string user, string exe)
        {
            user = SecurityElement.Escape(user);
            return "<?xml version=\"1.0\" encoding=\"UTF-16\"?>\r\n" +
                "<Task version=\"1.2\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">" +
                "<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>" + user + "</UserId></LogonTrigger></Triggers>" +
                "<Principals><Principal id=\"Author\"><UserId>" + user + "</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>" +
                "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" +
                "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>5</Priority></Settings>" +
                "<Actions Context=\"Author\"><Exec><Command>" + SecurityElement.Escape(exe) + "</Command></Exec></Actions></Task>";
        }

        static bool CreateTask()
        {
            string file = Path.GetTempFileName();
            try
            {
                File.WriteAllText(file, TaskXml(System.Security.Principal.WindowsIdentity.GetCurrent().Name, Application.ExecutablePath), Encoding.Unicode);
                string output;
                return Schtasks("/create /tn \"" + TaskName + "\" /xml \"" + file + "\" /f", out output) == 0;
            }
            finally { File.Delete(file); }
        }

        static int Schtasks(string args, out string output)
        {
            output = "";
            try
            {
                var info = new ProcessStartInfo("schtasks.exe", args)
                {
                    UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true
                };
                using (var p = Process.Start(info))
                {
                    output = p.StandardOutput.ReadToEnd();
                    p.WaitForExit();
                    return p.ExitCode;
                }
            }
            catch { return -1; }
        }
    }

    class StartupPrompt : ThemedForm
    {
        public StartupPrompt()
            : this("Start HDR Ultimate with Windows?",
                   "Hotkeys and game detection only work while HDR Ultimate is running in the tray. You can change this any time under Settings.",
                   "Yes, start with Windows") { }

        // A first-run yes/no question; Yes returns DialogResult.Yes.
        public StartupPrompt(string question, string detail, string yesText)
        {
            Text = Build.Name;
            Icon = AppIcon.Get();
            Font = HeaderFont.Body;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            TopMost = true;
            ClientSize = new Size(460, 180);

            var title = new HeaderLabel { Text = question, Font = HeaderFont.Get(13f), Location = new Point(18, 14), Size = new Size(424, 32), AutoEllipsis = true };
            int textHeight = TextRenderer.MeasureText(detail, Font, new Size(424, 0), TextFormatFlags.WordBreak).Height;
            var body = new Label
            {
                Text = detail,
                Location = new Point(18, 52), Size = new Size(424, Math.Max(66, textHeight + 6)), Tag = "muted"
            };
            ClientSize = new Size(460, body.Bottom + 62);
            var row = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 52, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(10) };
            var yes = new Button { Text = yesText, AutoSize = true, Tag = "primary", DialogResult = DialogResult.Yes };
            var no = new Button { Text = "Not now", AutoSize = true, DialogResult = DialogResult.No };
            row.Controls.AddRange(new Control[] { yes, no });
            Controls.AddRange(new Control[] { title, body, row });
            AcceptButton = yes;
            CancelButton = no;
        }
    }

    // ------------------------------------------------------------------ UI

    static class AppIcon
    {
        static readonly Dictionary<string, Icon> cache = new Dictionary<string, Icon>();

        // The app logo: the tray mark with every bar lit.
        public static readonly List<bool> AllOn = new List<bool> { true, true };

        public static Icon Get(int size = 32) { return ForState(AllOn, size); }

        // Tray icons are drawn at the exact size Windows shows them, so nothing gets rescaled.
        public static Icon ForState(List<bool> states) { return ForState(states, SystemInformation.SmallIconSize.Width); }

        static Icon ForState(List<bool> states, int size)
        {
            string key = Theme.Current.Bg.ToArgb() + ":" + size + ":" + string.Join("", states.Select(b => b ? "1" : "0"));
            Icon cached;
            if (cache.TryGetValue(key, out cached)) return cached;
            using (var bmp = Render(size, states))
            {
                var icon = Icon.FromHandle(bmp.GetHicon());
                cache[key] = icon;
                return icon;
            }
        }

        // A rainbow "H" and outline on a white (light) or black (dark) tile.
        // When every monitor has HDR off, the letter and outline go grey.
        // The bottom segments show each HDR-capable monitor (lit = HDR on), left to right in display order.
        public static Bitmap Render(int size, List<bool> states)
        {
            var p = Theme.Current;
            bool anyOn = states.Any(b => b);
            bool showSegs = states.Count > 0;
            var bmp = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
            var whole = new Rectangle(0, 0, size, size);
            Brush main = anyOn ? (Brush)Theme.Rainbow(whole, 45f) : new SolidBrush(p.Muted);
            using (main)
            using (var lit = Theme.Rainbow(whole, 45f))
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                float line = Math.Max(1f, size / 16f), edge = line / 2, d = size * 0.3f, far = size - edge - d;
                using (var tile = new GraphicsPath())
                {
                    tile.AddArc(edge, edge, d, d, 180, 90); tile.AddArc(far, edge, d, d, 270, 90);
                    tile.AddArc(far, far, d, d, 0, 90); tile.AddArc(edge, far, d, d, 90, 90);
                    tile.CloseFigure();
                    using (var br = new SolidBrush(p.Bg)) g.FillPath(br, tile);
                    using (var pen = new Pen(main, line)) g.DrawPath(pen, tile);
                }

                int pad = (int)Math.Ceiling(line) + Math.Max(1, size / 16);
                int barHeight = showSegs ? Math.Max(2, size / 7) : 0;
                int barGap = showSegs ? Math.Max(1, size / 16) : 0;
                var area = new RectangleF(pad, pad, size - 2 * pad, size - 2 * pad - barHeight - barGap);
                using (var letters = new GraphicsPath())
                using (var fit = new Matrix())
                {
                    var style = HeaderFont.Family.IsStyleAvailable(FontStyle.Bold) ? FontStyle.Bold : FontStyle.Regular;
                    letters.AddString("H", HeaderFont.Family, (int)style, 100f, Point.Empty, StringFormat.GenericTypographic);
                    var b = letters.GetBounds();
                    float scale = Math.Min(area.Width / b.Width, area.Height / b.Height);
                    fit.Translate(area.X + area.Width / 2, area.Y + area.Height / 2);
                    fit.Scale(scale, scale);
                    fit.Translate(-(b.X + b.Width / 2), -(b.Y + b.Height / 2));
                    letters.Transform(fit);
                    g.FillPath(main, letters);
                    using (var pen = new Pen(main, size / 26f)) g.DrawPath(pen, letters);
                }

                if (showSegs)
                {
                    g.SmoothingMode = SmoothingMode.None;
                    g.PixelOffsetMode = PixelOffsetMode.None;
                    int n = states.Count, gap = Math.Max(1, size / 16), width = size - 2 * pad;
                    int segW = Math.Max(1, (width - gap * (n - 1)) / n);
                    using (var off = new SolidBrush(p.Border))
                        for (int i = 0; i < n; i++)
                            g.FillRectangle(states[i] ? (Brush)lit : off, pad + i * (segW + gap), size - pad - barHeight, segW, barHeight);
                }
            }
            return bmp;
        }

        // The icon stored inside the exe (Explorer, shortcuts): one PNG per size.
        public static void WriteIco(string file)
        {
            int[] sizes = { 16, 24, 32, 48, 64, 128, 256 };
            var images = new List<byte[]>();
            foreach (int s in sizes)
                using (var bmp = Render(s, AllOn))
                using (var ms = new MemoryStream())
                {
                    bmp.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
                    images.Add(ms.ToArray());
                }
            using (var w = new BinaryWriter(File.Create(file)))
            {
                w.Write((short)0); w.Write((short)1); w.Write((short)sizes.Length);
                int offset = 6 + 16 * sizes.Length;
                for (int i = 0; i < sizes.Length; i++)
                {
                    w.Write((byte)(sizes[i] % 256)); w.Write((byte)(sizes[i] % 256)); w.Write((byte)0); w.Write((byte)0);
                    w.Write((short)1); w.Write((short)32); w.Write(images[i].Length); w.Write(offset);
                    offset += images[i].Length;
                }
                foreach (var img in images) w.Write(img);
            }
        }
    }

    // Brief top-right confirmation on the monitor that just switched to HDR. Never takes focus or clicks.
    class HdrBanner : Form
    {
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 3000 };

        public HdrBanner(MonitorInfo m, string detail)
        {
            var p = Theme.Current;
            Text = "HDR Ultimate banner";
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            BackColor = p.Bg;
            string line = string.IsNullOrEmpty(detail) ? m.Name : detail + "  ·  " + m.Name;
            var logo = new LogoMark(38) { Location = new Point(18, 14) };
            var title = new HeaderLabel { Text = "HDR ON", Font = HeaderFont.Get(12f), ForeColor = p.Text, Location = new Point(68, 10), Size = new Size(200, 24) };
            var body = new Label { Text = line, AutoSize = true, ForeColor = p.Muted, Location = new Point(66, 36) };
            Controls.AddRange(new Control[] { logo, title, body });
            ClientSize = new Size(Math.Max(250, body.PreferredWidth + 86), 66);
            var screen = Screen.AllScreens.FirstOrDefault(s => s.DeviceName == m.GdiName) ?? Screen.PrimaryScreen;
            Location = new Point(screen.WorkingArea.Right - Width - 16, screen.WorkingArea.Top + 16);
            Paint += (s, e) =>
            {
                using (var b = new SolidBrush(p.Accent)) e.Graphics.FillRectangle(b, 0, 0, 5, Height);
                using (var pen = new Pen(p.Border)) e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
            };
            timer.Tick += (s, e) => Close();
            timer.Start();
        }

        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                var cp = base.CreateParams;
                cp.ExStyle |= 0x8 | 0x80 | 0x20 | 0x08000000; // TOPMOST | TOOLWINDOW | TRANSPARENT | NOACTIVATE
                return cp;
            }
        }

        protected override void Dispose(bool disposing) { if (disposing) timer.Dispose(); base.Dispose(disposing); }
    }

    class Osd : Form
    {
        static Osd current;
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 1800 };

        public static void Flash(string text, int milliseconds = 1800)
        {
            if (current != null && !current.IsDisposed) current.Close();
            current = new Osd(text, milliseconds);
            current.Show();
        }

        Osd(string text, int milliseconds)
        {
            timer.Interval = milliseconds;
            var p = Theme.Current;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            BackColor = p.Bg;
            Opacity = 0.96;
            var lbl = new Label
            {
                Text = text, AutoSize = true, ForeColor = p.Text,
                Font = new Font("Segoe UI Semibold", 13f), Padding = new Padding(18, 12, 20, 12), Location = new Point(5, 1)
            };
            Controls.Add(lbl);
            ClientSize = new Size(lbl.PreferredSize.Width + 6, lbl.PreferredSize.Height + 2);
            Paint += (s, e) =>
            {
                using (var b = new SolidBrush(p.Accent)) e.Graphics.FillRectangle(b, 0, 0, 5, Height);
                using (var pen = new Pen(p.Border)) e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
            };
            var wa = Screen.FromPoint(Cursor.Position).WorkingArea;
            Location = new Point(wa.Left + (wa.Width - Width) / 2, wa.Bottom - Height - 80);
            timer.Tick += (s, e) => Close();
            timer.Start();
        }

        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                var cp = base.CreateParams;
                cp.ExStyle |= 0x8 | 0x80 | 0x20 | 0x08000000; // TOPMOST | TOOLWINDOW | TRANSPARENT | NOACTIVATE
                return cp;
            }
        }

        protected override void Dispose(bool disposing) { if (disposing) timer.Dispose(); base.Dispose(disposing); }
    }

    class HotkeyBox : TextBox
    {
        public Keys Key = Keys.None;
        public uint Mods;

        public HotkeyBox()
        {
            ReadOnly = true;
            BackColor = SystemColors.Window;
            ShortcutsEnabled = false;
            Cursor = Cursors.Arrow;
        }

        public void Set(uint mods, Keys key) { Mods = mods; Key = key; Text = Hotkeys.Format(mods, key); }

        protected override bool IsInputKey(Keys keyData) { return true; }

        static uint CurrentMods(KeyEventArgs e)
        {
            uint mods = 0;
            if (e.Control) mods |= Hotkeys.CTRL;
            if (e.Alt) mods |= Hotkeys.ALT;
            if (e.Shift) mods |= Hotkeys.SHIFT;
            if ((Native.GetKeyState(0x5B) & 0x8000) != 0 || (Native.GetKeyState(0x5C) & 0x8000) != 0) mods |= Hotkeys.WIN;
            return mods;
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            e.Handled = true; e.SuppressKeyPress = true;
            var k = e.KeyCode;
            uint mods = CurrentMods(e);
            if (k == Keys.ControlKey || k == Keys.ShiftKey || k == Keys.Menu || k == Keys.LWin || k == Keys.RWin)
            {
                Text = Hotkeys.Format(mods, Keys.None) + " + …";
                return;
            }
            if ((k == Keys.Back || k == Keys.Delete || k == Keys.Escape) && mods == 0) { Set(0, Keys.None); return; }
            Set(mods, k);
        }

        protected override void OnKeyUp(KeyEventArgs e)
        {
            e.Handled = true;
            Text = Key == Keys.None ? "" : Hotkeys.Format(Mods, Key);
        }
    }

    class SettingsForm : ThemedForm
    {
        readonly Config cfg;
        readonly ListView list, gameList;
        Panel oledGames;
        Label oledHint;
        readonly Func<List<string>> apply;   // saves + re-registers, returns hotkeys that failed
        readonly Action suspend;             // pauses hotkeys during capture
        List<string> failed = new List<string>();

        public SettingsForm(Config cfg, Func<List<string>> apply, Action suspend)
        {
            this.cfg = cfg;
            this.apply = apply;
            this.suspend = suspend;
            Text = Build.Name;
            Icon = AppIcon.Get();
            Font = HeaderFont.Body;
            ClientSize = new Size(720, 540);
            MinimumSize = new Size(620, 400);
            StartPosition = FormStartPosition.CenterScreen;
            KeyPreview = true;
            KeyDown += (s, e) => { if (e.KeyCode == Keys.Escape) Close(); };

            list = NewList(new[] { "Hotkey", "Monitors", "Status" }, new[] { 190, 320, 170 });
            list.MouseDoubleClick += (s, e) => HotkeyMenu(e.Location);
            gameList = Build.InGameHdrColumn // dev-only until the design is settled
                ? NewList(new[] { "Game", "Exe", "Monitors", "In-game HDR", "On launch" }, new[] { 190, 165, 95, 130, 100 })
                : NewList(new[] { "Game", "Exe", "Monitors", "On launch" }, new[] { 220, 190, 140, 130 });
            gameList.MouseDoubleClick += (s, e) => CellMenu(e.Location);

            var tabs = new Tabs { Dock = DockStyle.Fill };
            tabs.AddPage("HDR games", MakeTab("Double-click a cell to change it", gameList, Btn("Add game", AddGame, true)));
            var settingsTabs = new Tabs();
            settingsTabs.AddPage("General", MakeGeneral());
            settingsTabs.AddPage("Monitor hotkeys", MakeTab("Double-click a hotkey to edit or remove it", list, Btn("Add hotkey", Add, true)));
            settingsTabs.AddPage("OLED care", MakeOled());
#if DEV
            settingsTabs.AddPage("Dev", MakeDev());
#endif
            tabs.AddPage("Settings", settingsTabs);

            var brand = new Panel { Dock = DockStyle.Top, Height = 60 };
            brand.Controls.Add(new LogoMark(40) { Location = new Point(18, 14) });
            brand.Controls.Add(new HeaderLabel { Text = Build.Name, Font = HeaderFont.Get(15f), Location = new Point(70, 14), Size = new Size(400, 40) });

            Controls.Add(tabs);
            Controls.Add(brand);
            failed = apply();
            Refill();
        }

        static ListView NewList(string[] cols, int[] widths)
        {
            var lv = new ListView { View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false, Dock = DockStyle.Fill };
            for (int i = 0; i < cols.Length; i++) lv.Columns.Add(cols[i], widths[i]);
            return lv;
        }

        static Button Btn(string text, Action onClick, bool primary = false)
        {
            var b = new Button { Text = text, AutoSize = true, Tag = primary ? "primary" : null };
            b.Click += (s, e) => onClick();
            return b;
        }

        static Panel MakeTab(string info, ListView lv, Button add)
        {
            var page = new Panel { Padding = new Padding(18, 12, 18, 16) };
            var top = new Panel { Dock = DockStyle.Top, Height = 30 };
            add.AutoSize = false;
            add.Width = 110;
            add.Dock = DockStyle.Right;
            top.Controls.Add(new Label { Text = info, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, Tag = "muted" });
            top.Controls.Add(add);
            page.Controls.Add(lv);
            page.Controls.Add(new Panel { Dock = DockStyle.Top, Height = 10 });
            page.Controls.Add(top);
            return page;
        }

        // Label on the left, control on the right edge.
        static Panel Line(string text, Control box)
        {
            const int width = 640, height = 48;
            var row = new Panel { Dock = DockStyle.Top, Width = width, Height = height };
            row.Controls.Add(new Label { Text = text, AutoSize = true, Location = new Point(0, 15) });
            box.Location = new Point(width - box.Width, (height - box.Height) / 2);
            box.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            row.Controls.Add(box);
            return row;
        }

        static Toggle Switch(bool value, Action<bool> changed)
        {
            var t = new Toggle(value);
            t.Changed += changed;
            return t;
        }

        Control MakeGeneral()
        {
            var modes = new[] { "system", "light", "dark" };
            var theme = new SelectDropdown { Options = new[] { "Match Windows", "Light", "Dark" }, Selected = Math.Max(0, Array.IndexOf(modes, cfg.Theme)), Width = 200 };
            theme.Changed += () =>
            {
                cfg.Theme = modes[theme.Selected];
                cfg.Save();
                Theme.Set(cfg.Theme);
            };
            var page = new Panel { Padding = new Padding(18, 8, 18, 8), AutoScroll = true };
            var rows = new List<Panel>
            {
                Line("Theme", theme),
                Line("Start with Windows", Switch(Startup.Enabled, v =>
                {
                    if (!Startup.Set(v))
                        MessageBox.Show(this, "Windows didn't allow the faster startup task, so HDR Ultimate will use the standard startup instead. It may take a little longer to appear.", Build.Name);
                })),
                Line("On-screen notice", Switch(cfg.ShowOsd, v => { cfg.ShowOsd = v; cfg.Save(); })),
                Line("Auto HDR for games", Switch(cfg.GamesEnabled, v => { cfg.GamesEnabled = v; cfg.Save(); }))
            };
            if (!Build.Release)
            {
                var logs = Btn("Open folder", () => Process.Start("explorer.exe", "\"" + Config.Dir + "\""));
                logs.AutoSize = false;
                logs.Width = 120;
                rows.Add(Line("Logs  (" + Build.Channel + " " + Build.Version + ")", logs));
            }
            foreach (var row in Enumerable.Reverse(rows)) page.Controls.Add(row); // docked rows stack last-added first
            return page;
        }

        Control MakeOled()
        {
            var time = new Slider(Config.OledMinMinutes, Config.OledMaxMinutes, cfg.OledMinutes, v => v + " min") { Enabled = cfg.OledEnabled };
            time.Changed += v => { cfg.OledMinutes = v; cfg.Save(); };
            var page = new Panel { Padding = new Padding(18, 8, 18, 8), AutoScroll = true };
            oledGames = new Panel { Dock = DockStyle.Top, Enabled = cfg.OledEnabled };
            oledHint = new Label { Dock = DockStyle.Top, Height = 34, TextAlign = ContentAlignment.MiddleLeft, Tag = "muted" };
            page.Controls.Add(oledGames);
            page.Controls.Add(oledHint);
            page.Controls.Add(Line("Turn HDR off when tabbed out for", time));
            page.Controls.Add(Line("OLED care", Switch(cfg.OledEnabled, v =>
            {
                cfg.OledEnabled = v;
                cfg.Save();
                time.Enabled = v;
                oledGames.Enabled = v;
            })));
            return page;
        }

        void FillOledGames()
        {
            oledHint.Text = cfg.Games.Count == 0 ? "Add a game in HDR games first" : "Games";
            while (oledGames.Controls.Count > 0) oledGames.Controls[0].Dispose();
            foreach (var g in Enumerable.Reverse(cfg.Games)) // docked rows stack last-added first
            {
                var game = g;
                oledGames.Controls.Add(Line(g.Name, Switch(g.OledCare, v => { game.OledCare = v; cfg.Save(); })));
            }
            oledGames.Height = cfg.Games.Count * 48;
            Theme.Apply(oledGames);
        }

#if DEV
        // Fires each banner, notice and prompt on demand. None of these change real HDR state.
        Control MakeDev()
        {
            var page = new FlowLayoutPanel { Padding = new Padding(18, 12, 18, 12), AutoScroll = true };
            Func<MonitorInfo> first = () => Hdr.GetMonitors().FirstOrDefault() ?? new MonitorInfo { Name = "Test monitor" };
            Func<string> firstName = () => first().Name;
            var testGame = new Game { Name = "Test Game", ExePath = @"C:\Games\TestGame.exe" };
            Action<string, Action> add = (text, action) =>
            {
                var b = Btn(text, () => { Log.Write("Dev trigger: " + text); action(); });
                b.AutoSize = false;
                b.Size = new Size(200, 34);
                b.Margin = new Padding(0, 0, 10, 10);
                page.Controls.Add(b);
            };
            add("HDR banner", () => new HdrBanner(first(), "").Show());
            add("HDR banner: game", () => new HdrBanner(first(), testGame.Name).Show());
            add("HDR banner: all monitors", () => { foreach (var m in Hdr.GetMonitors()) new HdrBanner(m, "").Show(); });
            add("HDR off notice", () => Osd.Flash("HDR OFF  ·  " + firstName()));
            add("HDR failed notice", () => Osd.Flash("HDR didn't turn on  ·  " + firstName()));
            add("Game closed notice", () => Osd.Flash(testGame.Name + " closed  ·  HDR OFF  ·  " + firstName()));
            add("In-game HDR off notice", () => Osd.Flash(testGame.Name + ": HDR is off in the game's settings  ·  Windows HDR left off", 7000));
            add("OLED care notice", () => Osd.Flash("OLED care  ·  HDR OFF  ·  " + firstName()));
            add("Game prompt: HDR on", () => ShowGamePrompt(testGame, firstName(), true));
            add("Game prompt: unknown", () => ShowGamePrompt(testGame, firstName(), null));
            add("Startup prompt", () => { using (var p = new StartupPrompt()) Log.Write("Startup prompt answered: " + p.ShowDialog(this)); });
            add("Crash report", () => Log.Crash(new Exception("Test crash from the Dev tab"), true));
            return page;
        }

        static void ShowGamePrompt(Game game, string monitor, bool? inGame)
        {
            var prompt = new GamePrompt(game, new List<string> { monitor }, inGame);
            prompt.Answered += answer => Log.Write("Game prompt answered: " + answer);
            prompt.Show();
        }
#endif

        void Refill()
        {
            gameList.Items.Clear();
            foreach (var g in cfg.Games)
            {
                var item = new ListViewItem(g.Name);
                item.SubItems.Add(Path.GetFileName(g.ExePath));
                item.SubItems.Add(string.Join(", ", g.Monitors.Values));
                if (Build.InGameHdrColumn)
                {
                    bool? inGame = string.IsNullOrEmpty(g.ConfigPath) ? null : GameConfig.HdrEnabledIn(g.ConfigPath);
                    var state = item.SubItems.Add(inGame == true ? "On" : inGame == false ? "Off" : "—");
                    if (inGame == true) state.Tag = "accent";
                }
                item.SubItems.Add(g.Ask ? "Ask" : "Automatic");
                item.ToolTipText = g.ExePath;
                if (!File.Exists(g.ExePath)) item.ForeColor = Color.Gray;
                gameList.Items.Add(item);
            }
            gameList.ShowItemToolTips = true;
            FillOledGames();

            list.Items.Clear();
            foreach (var b in cfg.Bindings)
            {
                var item = new ListViewItem(b.HotkeyText);
                item.SubItems.Add(string.Join(", ", b.Monitors.Values));
                bool bad = failed.Contains(b.HotkeyText);
                var status = item.SubItems.Add(bad ? "Taken by another app" : "Active");
                if (!bad) status.Tag = "accent";
                if (bad) item.ForeColor = Color.Firebrick;
                list.Items.Add(item);
            }
        }

        void RunDialog(int index)
        {
            suspend();
            try
            {
                using (var dlg = new BindingDialog(index < 0 ? null : cfg.Bindings[index], cfg))
                {
                    if (dlg.ShowDialog(this) != DialogResult.OK) return;
                    if (index < 0) cfg.Bindings.Add(dlg.Result);
                    else cfg.Bindings[index] = dlg.Result;
                }
            }
            finally
            {
                failed = apply();
                Refill();
            }
        }

        void Add() { RunDialog(-1); }

        public ContextMenuStrip HotkeyMenu(Point location)
        {
            var hit = list.HitTest(location);
            if (hit.Item == null) return null;
            int row = hit.Item.Index;
            var pick = new SelectDropdown { Options = new[] { "Edit", "Remove" }, Selected = -1, Font = Font, Width = 160 };
            pick.Changed += () =>
            {
                if (pick.Selected == 0) { RunDialog(row); return; }
                cfg.Bindings.RemoveAt(row);
                failed = apply();
                Refill();
            };
            var menu = pick.BuildMenu();
            menu.Closed += (s, e) => BeginInvoke(new Action(() => { menu.Dispose(); pick.Dispose(); }));
            menu.Show(list, location);
            return menu;
        }

        public static string PickExe(IWin32Window owner, string current)
        {
            using (var ofd = new OpenFileDialog
            {
                Title = "Select the game's .exe",
                Filter = "Programs (*.exe)|*.exe",
                CheckFileExists = true,
                DereferenceLinks = true
            })
            {
                if (!string.IsNullOrEmpty(current) && Directory.Exists(Path.GetDirectoryName(current)))
                    ofd.InitialDirectory = Path.GetDirectoryName(current);
                return ofd.ShowDialog(owner) == DialogResult.OK ? Path.GetFullPath(ofd.FileName) : null;
            }
        }

        void AddGame()
        {
            string exe = PickExe(this, null);
            if (exe == null) return;
            var existing = cfg.Games.FindIndex(g => string.Equals(g.ExePath, exe, StringComparison.OrdinalIgnoreCase));
            RunGameDialog(existing >= 0 ? existing : -1, existing >= 0 ? null : new Game { ExePath = exe, Name = Game.NameFor(exe) });
        }

        void RunGameDialog(int index, Game fresh)
        {
            using (var dlg = new GameDialog(index < 0 ? fresh : cfg.Games[index], index < 0))
            {
                if (dlg.ShowDialog(this) != DialogResult.OK) return;
                if (index < 0) cfg.Games.Add(dlg.Result);
                else cfg.Games[index] = dlg.Result;
            }
            failed = apply();
            Refill();
        }

        // Double-clicking a cell in the games list opens that cell's options as a dropdown.
        public ContextMenuStrip CellMenu(Point location)
        {
            var hit = gameList.HitTest(location);
            if (hit.Item == null || hit.SubItem == null) return null;
            int row = hit.Item.Index, col = hit.Item.SubItems.IndexOf(hit.SubItem);
            var g = cfg.Games[row];
            Action saved = () =>
            {
                failed = apply();
                Refill();
                if (row < gameList.Items.Count) gameList.Items[row].Selected = true;
            };

            DropdownBase box;
            Action onClosed = null;
            if (col == 2)
            {
                var mons = new MonitorDropdown();
                MonitorEntry.Fill(mons.Add, g.Monitors, false);
                onClosed = () =>
                {
                    if (mons.Checked.Count == 0) return; // a game always needs at least one monitor
                    g.Monitors.Clear();
                    MonitorEntry.Collect(mons.Checked, g.Monitors);
                    saved();
                };
                box = mons;
            }
            else
            {
                SelectDropdown pick;
                if (col == 0) pick = GameActions(row);
                else if (col == 1) pick = ExeActions(g, saved);
                else if (gameList.Columns[col].Text == "In-game HDR") pick = ConfigActions(g, saved);
                else
                {
                    pick = new SelectDropdown { Options = new[] { "Ask me first", "Turn HDR on automatically" }, Selected = g.Ask ? 0 : 1 };
                    pick.Changed += () => { g.Ask = pick.Selected == 0; saved(); };
                }
                box = pick;
            }

            int x = 0;
            for (int i = 0; i < col; i++) x += gameList.Columns[i].Width;
            box.Font = Font;
            box.Width = gameList.Columns[col].Width;
            var menu = box.BuildMenu();
            menu.Closed += (s, e) =>
            {
                if (onClosed != null) onClosed();
                BeginInvoke(new Action(() => { menu.Dispose(); box.Dispose(); }));
            };
            menu.Show(gameList, new Point(x, hit.Item.Bounds.Bottom + 1));
            return menu;
        }

        SelectDropdown GameActions(int row)
        {
            var pick = new SelectDropdown { Options = new[] { "Rename / edit all options…", "Remove game" }, Selected = -1 };
            pick.Changed += () =>
            {
                if (pick.Selected == 0) { RunGameDialog(row, null); return; }
                cfg.Games.RemoveAt(row);
                failed = apply();
                Refill();
            };
            return pick;
        }

        SelectDropdown ExeActions(Game g, Action saved)
        {
            var pick = new SelectDropdown { Options = new[] { Path.GetFileName(g.ExePath), "Choose a different exe…", "Open file location" } };
            pick.Changed += () =>
            {
                if (pick.Selected == 2) Process.Start("explorer.exe", "/select,\"" + g.ExePath + "\"");
                if (pick.Selected != 1) return;
                string exe = PickExe(this, g.ExePath);
                if (exe == null) return;
                if (g.Name == Game.NameFor(g.ExePath)) g.Name = Game.NameFor(exe);
                g.ExePath = exe;
                g.ConfigPath = "";
                g.ConfigPath = GameConfig.Find(g) ?? "";
                saved();
            };
            return pick;
        }

        SelectDropdown ConfigActions(Game g, Action saved)
        {
            bool none = string.IsNullOrEmpty(g.ConfigPath);
            var pick = new SelectDropdown { Options = new[] { none ? "None found" : Path.GetFileName(g.ConfigPath), "Detect again", "Choose a file…" } };
            pick.Changed += () =>
            {
                if (pick.Selected == 1)
                {
                    g.ConfigPath = "";
                    g.ConfigPath = GameConfig.Find(g) ?? "";
                    saved();
                }
                else if (pick.Selected == 2)
                {
                    string file = GameDialog.PickConfig(this, g.ConfigPath);
                    if (file == null) return;
                    g.ConfigPath = file;
                    saved();
                }
            };
            return pick;
        }

    }

    class MonitorEntry
    {
        public string Path, Name, Label;
        public override string ToString() { return Label; }

        // Fills a checklist with connected monitors plus any saved ones that aren't connected right now.
        public static void Fill(Action<MonitorEntry, bool> add, Dictionary<string, string> selected, bool checkAllHdrIfEmpty)
        {
            var connected = Hdr.GetMonitors();
            foreach (var m in connected)
            {
                string label = m.Label + (m.HdrSupported ? (m.HdrOn ? "  — HDR on" : "  — HDR off") : "  — no HDR support");
                bool check = selected.Count == 0 ? (checkAllHdrIfEmpty && m.HdrSupported) : selected.ContainsKey(m.DevicePath);
                add(new MonitorEntry { Path = m.DevicePath, Name = m.Name, Label = label }, check);
            }
            foreach (var kv in selected)
                if (!connected.Any(m => m.DevicePath == kv.Key))
                    add(new MonitorEntry { Path = kv.Key, Name = kv.Value, Label = kv.Value + "  — not connected" }, true);
        }

        public static void Collect(List<MonitorEntry> chosen, Dictionary<string, string> into)
        {
            foreach (var en in chosen) into[en.Path] = en.Name;
        }
    }

    // Small corner prompt shown when an HDR game starts. Doesn't steal focus from the game.
    class GamePrompt : ThemedForm
    {
        public const int No = 0, Yes = 1, Always = 2;
        public event Action<int> Answered;
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 1000 };
        int secondsLeft = 25;
        bool done;

        public GamePrompt(Game g, List<string> monitors, bool? inGame)
        {
            Text = Build.Name;
            Icon = AppIcon.Get();
            Font = HeaderFont.Body;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = MinimizeBox = false;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            ClientSize = new Size(500, 140);

            string mons = string.Join(", ", monitors);
            var title = new HeaderLabel { Text = "Turn on HDR for " + g.Name + "?", Font = HeaderFont.Get(12.5f), Location = new Point(16, 10), Size = new Size(468, 32), AutoEllipsis = true };
            var body = new Label
            {
                Text = inGame == true ? "On in the game, off in Windows on " + mons : "Windows HDR is off on " + mons,
                Location = new Point(16, 44), Size = new Size(468, 26), AutoEllipsis = true, Tag = "muted"
            };
            var row = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 52, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(10) };
            var yes = new Button { Text = "Turn on HDR", AutoSize = true, Tag = "primary" };
            yes.Click += (s, e) => Finish(Yes);
            var always = new Button { Text = "Always for this game", AutoSize = true };
            always.Click += (s, e) => Finish(Always);
            var no = new Button { Text = "Not now", AutoSize = true };
            no.Click += (s, e) => Finish(No);
            row.Controls.AddRange(new Control[] { yes, always, no });
            Controls.AddRange(new Control[] { title, body, row });

            var wa = Screen.FromPoint(Cursor.Position).WorkingArea;
            Location = new Point(wa.Right - Width - 16, wa.Bottom - Height - 16);
            timer.Tick += (s, e) => { if (--secondsLeft <= 0) Finish(No); };
            timer.Start();
        }

        void Finish(int answer)
        {
            if (done) return;
            done = true;
            timer.Stop();
            if (Answered != null) Answered(answer);
            Close();
        }

        // Game closed before an answer: go away without doing anything.
        public void Dismiss() { done = true; timer.Stop(); Close(); }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!done) // closed with the X button
            {
                done = true;
                timer.Stop();
                if (Answered != null) Answered(No);
            }
            base.OnFormClosing(e);
        }
        protected override bool ShowWithoutActivation { get { return true; } }
        protected override CreateParams CreateParams
        {
            get { var cp = base.CreateParams; cp.ExStyle |= 0x8 | 0x80; return cp; } // TOPMOST | TOOLWINDOW
        }
        protected override void Dispose(bool disposing) { if (disposing) timer.Dispose(); base.Dispose(disposing); }
    }

    class GameDialog : ThemedForm
    {
        public Game Result;
        readonly TextBox name;
        readonly MonitorDropdown monitors;
        readonly SelectDropdown exeChoice, cfgChoice, onStart, ifOff, onClose, oledCare;
        string exePath, cfgPath;

        // Label on the left, control on the right.
        void Row(string label, int y, Control box)
        {
            Controls.Add(new Label { Text = label, Location = new Point(18, y + 7), AutoSize = true });
            box.Location = new Point(206, y);
            box.Width = 256;
            Controls.Add(box);
        }

        static SelectDropdown Choice(int selected, params string[] options)
        {
            return new SelectDropdown { Options = options, Selected = selected };
        }

        public GameDialog(Game g, bool isNew)
        {
            Text = isNew ? "Add HDR game" : "Edit HDR game";
            Icon = AppIcon.Get();
            Font = HeaderFont.Body;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            ClientSize = new Size(480, 382);
            exePath = g.ExePath;
            cfgPath = g.ConfigPath;

            name = new TextBox { Text = g.Name };
            exeChoice = Choice(0, Path.GetFileName(exePath ?? ""), "Choose a different exe…");
            exeChoice.Changed += () =>
            {
                string previous = exePath;
                string p = exeChoice.Selected == 1 ? SettingsForm.PickExe(this, exePath) : null;
                exeChoice.Selected = 0;
                if (p == null) return;
                if (name.Text == Game.NameFor(previous)) name.Text = Game.NameFor(p);
                exePath = p;
                exeChoice.Options = new[] { Path.GetFileName(p), "Choose a different exe…" };
                Detect();
            };

            monitors = new MonitorDropdown();
            cfgChoice = new SelectDropdown();
            cfgChoice.Changed += () =>
            {
                if (cfgChoice.Selected == 1) Detect();
                else if (cfgChoice.Selected == 2) BrowseConfig();
            };
            onStart = Choice(g.Ask ? 0 : 1, "Ask me first", "Turn HDR on automatically");
            ifOff = Choice(g.SkipIfOff ? 0 : 1, "Leave Windows HDR off", "Offer HDR anyway");
            onClose = Choice(g.Restore ? 0 : 1, "Turn HDR back off", "Leave HDR on");
            oledCare = Choice(g.OledCare ? 1 : 0, "Keep HDR on", "Turn HDR off (OLED care)");

            int y = 18;
            Action<string, Control> next = (label, box) => { Row(label, y, box); y += 40; };
            next("Name", Theme.Field(name, 256));
            next("Executable", exeChoice);
            next("Monitors", monitors);
            next("Game settings file", cfgChoice);
            next("When the game starts", onStart);
            next("If HDR is off in the game", ifOff);
            next("When the game closes", onClose);
            next("When tabbed out", oledCare);
            ClientSize = new Size(480, y + 84);

            var ok = new Button { Text = "Save", Location = new Point(296, y + 32), Width = 80, Tag = "primary" };
            ok.Click += (s, e) => Accept();
            var cancel = new Button { Text = "Cancel", Location = new Point(382, y + 32), Width = 80, DialogResult = DialogResult.Cancel };
            AcceptButton = ok;
            CancelButton = cancel;
            Controls.AddRange(new Control[] { ok, cancel });

            MonitorEntry.Fill(monitors.Add, g.Monitors, isNew);
            if (string.IsNullOrEmpty(g.ConfigPath) || !File.Exists(g.ConfigPath)) Detect();
            else ShowStatus();
        }

        void Detect()
        {
            if (!string.IsNullOrEmpty(exePath))
            {
                Cursor = Cursors.WaitCursor;
                cfgPath = GameConfig.Find(new Game { ExePath = exePath, Name = name.Text }) ?? "";
                Cursor = Cursors.Default;
            }
            ShowStatus();
        }

        public static string PickConfig(IWin32Window owner, string current)
        {
            using (var ofd = new OpenFileDialog { Title = "Select the game's settings file", Filter = "Settings files|*.ini;*.json;*.xml;*.cfg;*.txt;*.config;*.settings;*.lua;*.yaml;*.yml|All files|*.*" })
            {
                if (File.Exists(current)) ofd.InitialDirectory = Path.GetDirectoryName(current);
                return ofd.ShowDialog(owner) == DialogResult.OK ? ofd.FileName : null;
            }
        }

        void BrowseConfig()
        {
            cfgPath = PickConfig(this, cfgPath) ?? cfgPath;
            ShowStatus();
        }

        void ShowStatus()
        {
            bool none = string.IsNullOrEmpty(cfgPath);
            bool? on = none ? null : GameConfig.HdrEnabledIn(cfgPath);
            string state = on == true ? "  ·  HDR on" : on == false ? "  ·  HDR off" : "";
            cfgChoice.Options = new[] { none ? "None found" : Path.GetFileName(cfgPath) + state, "Detect again", "Choose a file…" };
            cfgChoice.Selected = 0;
        }

        void Accept()
        {
            if (string.IsNullOrEmpty(exePath)) { MessageBox.Show(this, "Pick the game's .exe first.", Text); return; }
            if (monitors.Checked.Count == 0) { MessageBox.Show(this, "Pick at least one monitor.", Text); return; }
            Result = new Game
            {
                ExePath = exePath,
                Name = string.IsNullOrWhiteSpace(name.Text) ? Path.GetFileNameWithoutExtension(exePath) : name.Text.Trim(),
                ConfigPath = cfgPath,
                Restore = onClose.Selected == 0,
                Ask = onStart.Selected == 0,
                SkipIfOff = ifOff.Selected == 0,
                OledCare = oledCare.Selected == 1
            };
            MonitorEntry.Collect(monitors.Checked, Result.Monitors);
            DialogResult = DialogResult.OK;
        }
    }

    class BindingDialog : ThemedForm
    {
        public Binding Result;
        readonly Binding original;
        readonly Config cfg;
        readonly HotkeyBox hotkey;
        readonly MonitorDropdown monitors;

        public BindingDialog(Binding existing, Config cfg)
        {
            original = existing;
            this.cfg = cfg;
            Text = existing == null ? "Add hotkey" : "Edit hotkey";
            Icon = AppIcon.Get();
            Font = HeaderFont.Body;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            ClientSize = new Size(480, 164);

            var l1 = new Label { Text = "Press your hotkey", Location = new Point(18, 25), AutoSize = true };
            hotkey = new HotkeyBox();
            var field = Theme.Field(hotkey, 256);
            field.Location = new Point(206, 18);
            var l2 = new Label { Text = "Monitors", Location = new Point(18, 65), AutoSize = true };
            monitors = new MonitorDropdown { Location = new Point(206, 58), Width = 256 };

            var ok = new Button { Text = "Save", Location = new Point(296, 112), Width = 80, Tag = "primary" };
            ok.Click += (s, e) => Accept();
            var cancel = new Button { Text = "Cancel", Location = new Point(382, 112), Width = 80, DialogResult = DialogResult.Cancel };
            CancelButton = cancel;
            Controls.AddRange(new Control[] { l1, field, l2, monitors, ok, cancel });

            MonitorEntry.Fill(monitors.Add, existing != null ? existing.Monitors : new Dictionary<string, string>(), false);
            if (existing != null) hotkey.Set(existing.Mods, existing.Key);
        }

        protected override void OnShown(EventArgs e) { base.OnShown(e); hotkey.Focus(); }

        void Accept()
        {
            if (hotkey.Key == Keys.None) { MessageBox.Show(this, "Press a key combination in the hotkey box first.", Text); return; }
            if (monitors.Checked.Count == 0) { MessageBox.Show(this, "Pick at least one monitor.", Text); return; }
            var dup = cfg.Bindings.FirstOrDefault(b => b != original && b.Mods == hotkey.Mods && b.Key == hotkey.Key);
            if (dup != null) { MessageBox.Show(this, "That hotkey is already used by another entry.", Text); return; }

            Result = new Binding { Mods = hotkey.Mods, Key = hotkey.Key };
            MonitorEntry.Collect(monitors.Checked, Result.Monitors);
            DialogResult = DialogResult.OK;
        }
    }
}
