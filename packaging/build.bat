chcp 65001 >nul
@echo off
rem 一鍵打包：dist\PhotoManager\（含 PhotoManager.exe 的資料夾）+ dist\installer\PhotoManager-Setup-版本.exe
setlocal
cd /d "%~dp0.."
for /f "tokens=2 delims==" %%v in ('findstr /b "__version__" photomanager\__init__.py') do set VER=%%~v
set VER=%VER: =%
set VER=%VER:"=%
echo === Photo Manager %VER% ===

python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller || goto :fail
python packaging\make_icon.py || goto :fail
python -m PyInstaller --noconfirm --clean PhotoManager.spec || goto :fail

set ISCC=
for %%p in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist %%p set ISCC=%%p
if not defined ISCC (
  echo 找不到 Inno Setup 6，只產生資料夾版。安裝：winget install JRSoftware.InnoSetup
  goto :done
)
%ISCC% /Qp /DAppVersion=%VER% packaging\installer.iss || goto :fail

:done
echo.
echo 完成：
echo   dist\PhotoManager\PhotoManager.exe
if defined ISCC echo   dist\installer\PhotoManager-Setup-%VER%.exe
exit /b 0
:fail
echo 打包失敗
exit /b 1
