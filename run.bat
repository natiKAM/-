@echo off
echo התקנת תלויות...
pip install -r requirements.txt
echo.
echo מפעיל את השרת...
echo פתח את הדפדפן בכתובת: http://localhost:5000
echo.
python app.py
pause
