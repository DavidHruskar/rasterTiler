@echo off
REM run.bat - Skripta za pokretanje Pyramid Tool aplikacije

echo ============================================
echo Pyramid Tool - GeoTIFF Processor
echo ============================================
echo.

REM Provjeri postoji li virtualno okruženje
if not exist ".venv\Scripts\activate.bat" (
    echo GRESKA: Virtualno okruženje nije pronađeno!
    echo Prvo pokrenite setup_venv.bat za postavljanje okruzenja.
    echo.
    pause
    exit /b 1
)

REM Aktiviraj virtualno okruženje
call .venv\Scripts\activate.bat

REM Pokreni aplikaciju
echo Pokretanje aplikacije...
echo.
python main.py

REM Deaktiviraj nakon zatvaranja
deactivate