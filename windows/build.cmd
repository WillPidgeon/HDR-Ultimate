@echo off
rem Builds HDR Ultimate with the C# compiler built into Windows (.NET Framework 4.x).
rem   build.cmd            release   HDRUltimate.exe
rem   build.cmd dev        dev       builds\Dev\HDRUltimate-Dev.exe (console log, Dev tab with event triggers)
rem   build.cmd playtest   playtest  builds\Playtest\HDRUltimate-Playtest.exe (log file, crash reports)
rem   build.cmd all        all three
rem HDRUltimate.ico comes from "HDRUltimate.exe --make-icon HDRUltimate.ico".
cd /d "%~dp0"
set MODE=%~1
if "%MODE%"=="" set MODE=release

if /i "%MODE%"=="all" (
  call "%~f0" release || exit /b 1
  call "%~f0" dev || exit /b 1
  call "%~f0" playtest || exit /b 1
  exit /b 0
)

set OUT=HDRUltimate.exe
set TARGET=winexe
set DEFINE=
if /i "%MODE%"=="dev" (
  set OUT=builds\Dev\HDRUltimate-Dev.exe
  set TARGET=exe
  set DEFINE=/define:DEV
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
  /win32icon:HDRUltimate.ico /resource:Orbitron.ttf,Orbitron.ttf ^
  /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll HDRUltimate.cs
if errorlevel 1 (echo Build failed. & exit /b 1)
echo Built %OUT%
