@echo off
chcp 65001 >nul
rem 聊书 liaoshu —— Windows 一键打包脚本
rem 需要：Python 3.9+（安装时勾 "Add Python to PATH"）
rem 双击即可：会自己装 pywebview + pyinstaller，然后打出 dist\liaoshu.exe

setlocal
cd /d "%~dp0\.."

set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  py --version >nul 2>&1 && set "PY=py"
)
if not defined PY (
  echo [聊书] 没找到 Python。先装 Python 3.9+：https://www.python.org/downloads/
  echo         安装时务必勾选 "Add Python to PATH"。
  pause
  exit /b 1
)

echo [聊书] 检查打包依赖……
"%PY%" -c "import PyInstaller, webview" >nul 2>&1
if errorlevel 1 (
  echo [聊书] 正在安装 pywebview + pyinstaller（国内镜像，稍等）……
  "%PY%" -m pip install --upgrade pywebview pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 (
    echo [聊书] 镜像装失败，换官方源再试一次……
    "%PY%" -m pip install --upgrade pywebview pyinstaller
    if errorlevel 1 (
      echo [聊书] 依赖装不上，看上面的报错（多半是网络），重试一次往往就好。
      pause
      exit /b 1
    )
  )
)

echo [聊书] 开始打包（第一次会久一点）……
"%PY%" -m PyInstaller packaging\liaoshu.spec --noconfirm --clean
if errorlevel 1 (
  echo [聊书] 打包失败，看上面的红字。
  pause
  exit /b 1
)

echo.
echo [聊书] 完成！产物：dist\liaoshu.exe
echo         这个 exe 可以直接发给别人：双击即可，不需要装 Python。
echo         运行环境：Windows 10/11 自带 WebView2；老系统需装一次
echo         "Microsoft Edge WebView2 Runtime"。
echo.
pause
exit /b 0
