@echo off
rem Builds HDR Ultimate with the C# compiler built into Windows (.NET Framework 4.x).
rem   build.cmd            release   HDRUltimate.exe
rem   build.cmd dev        dev       builds\Dev\HDRUltimate-Dev-<version>.exe (console log, Dev tab with event triggers);
rem                                  each version keeps its own file, so older dev builds stay
rem   build.cmd playtest   playtest  builds\Playtest\HDRUltimate-Playtest.exe (log file, crash reports)
rem   build.cmd all        all three
rem HDRUltimate.ico comes from "HDRUltimate.exe --make-icon HDRUltimate.ico", and the dev "D" icon,
rem HDRUltimate-Dev.ico, from running a dev build with --make-icon HDRUltimate-Dev.ico.
cd /d "%~dp0"
set MODE=%~1
if "%MODE%"=="" set MODE=release

if /i "%MODE%"=="all" (
  call "%~f0" release || exit /b 1
  call "%~f0" dev || exit /b 1
  call "%~f0" playtest || exit /b 1
  exit /b 0
)

rem The version, from: public const string Version = "1.3.1";
for /f tokens^=2^ delims^=^" %%v in ('findstr /c:"public const string Version" HDRUltimate.cs') do set VERSION=%%v

set OUT=HDRUltimate.exe
set TARGET=winexe
set DEFINE=
set ICON=HDRUltimate.ico
if /i "%MODE%"=="dev" (
  set OUT=builds\Dev\HDRUltimate-Dev-%VERSION%.exe
  set TARGET=exe
  set DEFINE=/define:DEV
  if exist HDRUltimate-Dev.ico set ICON=HDRUltimate-Dev.ico
  if not exist builds\Dev mkdir builds\Dev
) else if /i "%MODE%"=="playtest" (
  set OUT=builds\Playtest\HDRUltimate-Playtest.exe
  set DEFINE=/define:PLAYTEST
  if not exist builds\Playtest mkdir builds\Playtest
) else if /i not "%MODE%"=="release" (
  echo Unknown build "%MODE%". Use release, dev, playtest or all.
  exit /b 1
)

"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /optimize /nowarn:162,429 /target:%TARGET% /out:%OUT% %DEFINE% ^
  /win32icon:%ICON% /resource:Orbitron.ttf,Orbitron.ttf ^
  /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:Microsoft.VisualBasic.dll HDRUltimate.cs
if errorlevel 1 (echo Build failed. & exit /b 1)
echo Built %OUT%
