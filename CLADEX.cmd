@echo off
setlocal
set "ROOT=%~dp0"
set "APP=%ROOT%release\win-unpacked\CLADEX.exe"

if exist "%APP%" (
  start "" "%APP%"
  exit /b 0
)

pushd "%ROOT%"
call npm run app
popd
