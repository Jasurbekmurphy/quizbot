# Test (quiz) boti

QuizBot kabi ishlaydi, lekin savollar bittalab emas, bitta Excel yoki Word fayldan birdaniga yuklanadi.

## Imkoniyatlar
- Excel (.xlsx), Word (.docx) yoki .txt fayldan testni bir yo'la yuklash. Namuna: `/shablon`.
- Har savolga vaqt: 10 soniyadan 5 daqiqagacha.
- Savollar va javoblarni har safar aralashtirish (yoqish/o'chirish mumkin).
- Testni shaxsiy chatda yechish yoki guruhda o'tkazish: guruhda oxirida reyting (to'g'ri javoblar soni, keyin tezlik).
- Havola orqali ulashish, guruh mavzulari (topic) ichida ham ishlaydi.
- `/stop` — testni to'xtatish (boshlagan odam, test egasi yoki guruh admini).

## Fayl ko'rinishlari
**Excel:** A — savol, B — to'g'ri javob, C, D, E... — noto'g'ri javoblar, oxirgi ustun «Izoh» (ixtiyoriy).

**Word / .txt:**
```
? Savol matni
+ To'g'ri javob
- Noto'g'ri javob
- Noto'g'ri javob
Izoh: ixtiyoriy
```
Shuningdek: `1. Savol` + `A) ... *B) to'g'ri ...` yoki oxirida `Javob: B`, hamda HEMIS formati (`====`, `#to'g'ri`, `++++`).

## O'rnatish (VPS)
```
git clone <repo> /opt/quiz-bot
cd /opt/quiz-bot && bash install.sh
nano .env      # BOT_TOKEN ni yozing
systemctl restart quiz-bot
```
`.env` da `ADMIN_IDS` bo'sh bo'lsa, har kim o'z testini yarata oladi; yozilsa — faqat shu odamlar. Testni yechish hamma uchun ochiq.
