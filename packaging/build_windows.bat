@echo off
rem 聊书 liaoshu —— Windows 一键打包脚本
rem 需要：Python 3.9+、pip install pywebview pyinstaller
rem 双击运行，产物在 dist\liaoshu.exe

cd /d %~dp0\..

python -m pip install --upgrade pywebview pyinstaller || goto :err
python -m PyInstaller packaging\liaoshu.spec --noconfirm || goto :err

echo.
echo 打包完成：dist\liaoshu.exe
echo 首次使用需要 Windows 10/11 自带（或装一次）Edge WebView2 运行时。
pause
exit /b 0

:err
echo 打包失败，看上面的报错。
pause
exit /b 1