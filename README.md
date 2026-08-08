# NXN Branch Audit — Python v2

نظام تدقيق فروع عملي مبني بالكامل بلغة Python القياسية + SQLite، بدون أي مكتبات خارجية.

## الوظائف
- واجهة عربية / إنجليزية ومتجاوبة
- صلاحيات: مدير نظام / مدقق / مدير فرع
- إدارة الفروع والمستخدمين
- إنشاء زيارات تدقيق
- نموذج تدقيق مقسم إلى أقسام وأسئلة وأوزان
- إجابات: ملتزم / جزئي / غير ملتزم / غير منطبق
- احتساب النتيجة تلقائياً حسب الأوزان
- ملاحظات لكل سؤال وملاحظات عامة للزيارة
- دورة حالات: draft → submitted → reviewed → closed
- إجراءات تصحيحية مع مسؤول وتاريخ استحقاق وحالة
- Dashboard وتقارير أداء حسب الفرع
- تصدير التقرير CSV
- SQLite تُنشأ وتُحدّث تلقائياً

## التشغيل
```bash
python app.py
```
ثم افتح:
http://127.0.0.1:5000

## الحسابات التجريبية
- manager@nxn.local — System Manager
- auditor@nxn.local — Auditor
- branch@nxn.local — Branch Manager

> قبل الإنتاج الحقيقي يوصى باستبدال تسجيل الدخول التجريبي بـ SSO/هوية الشركة، تشغيل HTTPS، واستخدام مخزن جلسات دائم.

## Dashboard v3
The dashboard is now connected to live SQLite data and includes quality score, total audits, compliance rate, open corrective actions, branch ranking, workflow status distribution, recent audits, attention items, and manager quick actions.
