# Test (quiz) boti

Paxtaobod tumani 2-son texnikumi uchun test boti. Savollar bitta Excel, Word yoki .txt fayldan birdaniga yuklanadi yoki botda birma-bir yoziladi.

## Imkoniyatlar
- Excel (.xlsx), Word (.docx) yoki .txt fayldan testni bir yo'la yuklash. Namuna: «📄 Shablonlar» tugmasi.
- Savollarni botning o'zida qo'lda kiritish («✍️ Qo'lda kiritish»): matn, variantlar, to'g'ri javob va vaqt. Telegramning tayyor viktorina oynasida tuzilgan savol ham qabul qilinadi.
- Vaqt: hamma savollarga bir xil yoki har bir savolga alohida, 5 soniyadan 10 daqiqagacha istalgan son. Savollar ro'yxatidan («⚙️ Sozlamalar» → «⏱ Savollar va vaqtlari») istalgan savolning vaqtini o'zgartirish mumkin.
- Savollar va javoblarni har safar aralashtirish (yoqish/o'chirish mumkin).
- Testni shaxsiy chatda yechish yoki guruhda o'tkazish: guruhda oxirida reyting (to'g'ri javoblar soni, keyin tezlik).
- Havola orqali ulashish, guruh mavzulari (topic) ichida ham ishlaydi.
- «📊 Statistika» (faqat `.env` dagi `OWNER_IDS` uchun): foydalanuvchilar soni (24 soat / 7 / 30 kun), testlar, yechishlar, guruhlar.
- «⏹ To'xtatish» tugmasi — testni to'xtatish (boshlagan odam, test egasi yoki guruh admini).

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
