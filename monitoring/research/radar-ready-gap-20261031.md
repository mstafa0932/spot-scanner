# لماذا يرى الرادار الحركة ولا تصبح توصية؟ — السجل المتاح حتى 31 أكتوبر 2026

**ثبت اختلاف وظيفتين في البوت: الرادار يرتّب الأسواق التي زاد سعرها ونشاطها، بينما READY تشترط أهلية الدخول في اللحظة نفسها. في أمثلة متكررة تتحسن الحركة قبل تحسن الاتجاه الأعلى، ثم تظهر موانع RSI أو المطاردة لاحقًا.** هذا يفسّر مسارات رفض موثقة، ويحدد مشكلة توقيت محتملة في التصميم؛ لا يثبت أن إلغاء أحد الشروط سيولد توصية أفضل أو أن إصلاحًا جديدًا اكتمل.

هذه مراجعة منفصلة للقراءة فقط. كود البوت وإعداداته وحالته وSHADOW والـworkflows لم تُمس؛ لا رسائل أو أوامر أو dispatch أو دمج. تعليمات المستخدم بتاريخ29أكتوبر بعدم تعديل البوت تتقدم على تفويضات الإصلاح الأقدم داخل WORK_POLICY.

## ما جُمع فعلًا وحدود استرجاع الشهر

- جرى دمج **2493 قراءة مؤرخة فريدة** من **14أكتوبر00:07:50 إلى31أكتوبر06:36:31** بتوقيت تركيا. أُعيد استخدام المدخلات المحفوظة، ولم يُعد تنزيل أرشيف الأيام الـ15 من المنصة.
- أصبح الفهرس **50 مدخل سوق/سياق**، ويشمل سياق BTC وزوجي MORPHO وTRX المصورين بـUSDT ومقارنة CFG المشروطة أدناه. ليس هذا عدد50انفجارًا أو50توصية صحيحة ضائعة.
- رُبطت تقارير XCN وALT وMASK والحالات المقارنة، والتحقيقات اللاحقة، والصور المسجلة في30–31أكتوبر. لكل مصدر مسار وSHA وبصمة في [ملف الأدلة](radar-ready-gap-20261031.json).
- تعذر استرجاع سجل المحادثات الأقدم بأداة السياق الشخصي. لا نزعم استعادة جميع صور الشهر أو امتلاك دفاتر أوامر لكل لحظة؛ نستخدم كل ما أمكن استرجاعه من السجل المحفوظ، ونترك الناقص مجهولًا.
- صُحح التباس التسجيل الأولي لصورة **CF**: لا يوجد CF_TL في المدخلات المفحوصة. [صفحة Paribu الرسمية لـCentrifuge](https://www.paribu.com/fiyat/centrifuge) تسمي السوق CFG وتعرض نطاقًا مطابقًا7.830/3.982، لكن معرف سوق الصورة الخام غير محفوظ. لذلك بيانات CFG_TL هنا **مقارنة مشروطة**، وليست إعلان اكتشاف خطأ في تغطية البوت أو تطابق قطعي. الصفحة الحالية لم تدخل أي قرار تاريخي.

## السبب البرمجي المباشر للفجوة

المرجع الحالي للكود `0d3942b8e326a13124b85567c16feb6c0feed922`. [الرادار](https://github.com/mstafa0932/spot-scanner/blob/0d3942b8e326a13124b85567c16feb6c0feed922/scanner.py#L281) لا يتنبأ بالانفجار: يشترط حجمًا لا يقل5M، وارتفاع السعر وارتفاع حجم24ساعة الدوار بين قراءتين، ثم يرتّب حاصل ضرب نسبتي التغير. فرق الحجم الدوار ليس حجم شراء منفذًا داخل الفترة.

بعد ذلك تمر الحالة ببوابات متتابعة: السعة والسيولة، دفتر الأوامر والسبريد، صحة شموع Paribu المغلقة، discovery، ثم trigger وBTC والتأكيدات، ثم فحص دفتر جديد والمخاطرة وحدود الإرسال. [المصدر](https://github.com/mstafa0932/spot-scanner/blob/0d3942b8e326a13124b85567c16feb6c0feed922/scanner.py#L1874) يتوقف عند أول مانع؛ عدم حفظ مؤشرات ما بعد المانع لا يعني أنها كانت ناجحة.

[درجة80/100](https://github.com/mstafa0932/spot-scanner/blob/0d3942b8e326a13124b85567c16feb6c0feed922/scanner.py#L845) تجمع شروط سيولة وزخم واتجاه حالي؛ ليست احتمال80% لصعود لاحق. ويمكن أن ترفض [بوابة READY](https://github.com/mstafa0932/spot-scanner/blob/0d3942b8e326a13124b85567c16feb6c0feed922/scanner.py#L1476) حالة درجتها85 بسبب شرط مستقل. استثناء الانفجار الموجود منذ قبل هذه المراجعة يسمح بتأكيد واحد بشروطه؛ لذلك ليس صحيحًا أن تأكيدين سبب عام لكل تأخر.

الفارق الزمني موثق: discovery تشترط إغلاق4h فوق0.97×EMA50، وREADY فوق0.98×EMA50. هذه قيم آخر شمعة4h **مغلقة**؛ لا يغيرها ارتفاع الشمعة الجارية حتى إغلاقها. وقد تصبح RSI أو امتداد15m مرتفعين خلال هذه المدة. توجد كذلك موانع تنفيذ مبكرة مستقلة؛ لا يجوز اختزال كل حالة في4h.

## أمثلة تربط الصور بوقت القرار

| الحالة | ما ثبت في وقت القرار | دلالة الإصلاح |
| --- | --- | --- |
| XCN،18أكتوبر20:15 | درجة78 وBTC مقبول، لكن4h المغلقة0.1978 دون حد discovery0.2022078؛ لاحقًا RSI مرتفع. المتابعة استمرت ولم تفقد TTL. | تعارض توقيت محتمل؛ فتح4h وحدها لا يكفي لأن الدرجة وشروطًا أخرى لم تجتز. |
| MASK،22أكتوبر12:41–13:41 | السعر22.05→24.17 بينما حجم التداول دون5M؛ بعد اجتيازه أصبح السبريد0.966608%. | جزء من الحركة وقع قبل أهلية السيولة؛ دفتر الأوامر الأقدم غير محفوظ. |
| LIT وMEGA وSENT | أمثلة موثقة تحت حد5M؛ MEGA259من291دورة، وSENT223من223دورة في نوافذهما السابقة. | حالات لا تصل أصلًا إلى كل فحوص READY؛ ليست رسائل جاهزة ضاعت. |
| BEAM،30أكتوبر07:03 | HOT أصلي بسعر0.1715 وBTC مقبول؛ السبريد0.468658% فوق0.40%. | مانع تنفيذ متزامن، ثم ظهرت موانع RSI/المطاردة لاحقًا. |
| TLM،30أكتوبر07:23 | HOT أصلي بسعر0.0560؛ اختلال الدفتر0.632590 دون0.85، معBTC مقبول. | بداية الرصد محفوظة؛ لا يوجد اجتياز مثبت لبقية الشروط في تلك القراءة. |
| OP،30أكتوبر15:06 | HOT أصلي؛ درجة57 و4h ضعيفة وBTC غير مقبول. عند23:06 أصبحت الدرجة81 لكنRSI74.8712. | مثال جديد على اختلاف توقيت الشروط؛ رفع RSI بعد الحركة لا يحل سبب التأخر المبكر. |
| HOT،30أكتوبر11:04→15:06 | عند11:04 درجة57 و1h ضعيفة؛ عند15:06 RSI80.8781 ودرجة70 وBTC مرفوض. | الحركة والدرجة لم تتحولا إلى أهلية دخول كاملة في العينات. |
| XPL،31أكتوبر00:06 | تشغيل41128281669: HOT وBTC مقبول ودرجة85، وصلت إلى المخاطرة. دخول مرجعي3.9825 ووقف3.805603؛ مخاطرة4.44185775% مقابل سقف4%. | رفض خطر حسابي موثق، وليس بلوغ85 وحده إذنًا لإرسال توصية. |
| IMX،30أكتوبر00:03–31أكتوبر06:36 | **184من184**رفض سيولة؛ الحجم0.913–3.264M فقط. لا HOT مؤهلة أو تحليل فني محفوظ في هذه النافذة. | وجود السعر في ticker يختلف عن دخول HOT؛ لا نختلق RSI أو دفترًا غير مفحوص. |
| CFG كمرشح لصورةCF | **184من184**رفض سيولة؛ الحجم2.533–4.584M. | استنتاج شرطي إلى أن تثبت هوية الصورة؛ لا CF_TL في المدخلات. |
| SPK في النافذة نفسها |50رفض دفتر و90تأجيل سعة و17رفضRSI و11رفض4h؛ أعلى درجة محفوظة79. | أكثر من مانع، ولا READY محفوظة. التأجيل وحده لا يثبت أهلية تاريخية مفقودة. |

تفاصيل184دورة لكل سوق جديد، عضويةHOT الأصلية، معرفات التشغيل، الحالات الثابتة ومؤشرات الشمعة المغلقة محفوظة في ملف الأدلة. أوائلHOT المذكورة فيه تعني أول مشاهدة داخل النافذة المفحوصة، لا بداية الانفجار أو أول رصد في تاريخ العملة.

المراجع السابقة المعاد استخدامها: [XCN](https://github.com/mstafa0932/spot-scanner/blob/f54320311d818d7cc0f41dacb3b64364c54055f7/monitoring/cases/XCN_TL_20261018.md)، [MASK والمقارنة](https://github.com/mstafa0932/spot-scanner/blob/f54320311d818d7cc0f41dacb3b64364c54055f7/monitoring/cases/MASK_TL_20261022.md)، [SAFA/SFA/FET](https://github.com/mstafa0932/spot-scanner/blob/f54320311d818d7cc0f41dacb3b64364c54055f7/monitoring/cases/SAFA_SFA_FET_20261029.md)، [REEF/T](https://github.com/mstafa0932/spot-scanner/blob/f54320311d818d7cc0f41dacb3b64364c54055f7/monitoring/cases/REEF_T_20261029.md)، [TLM/BEAM](https://github.com/mstafa0932/spot-scanner/blob/f54320311d818d7cc0f41dacb3b64364c54055f7/monitoring/research/tlm-beam-evidence-20261030.md).

## عدم خلط رفض التوصية بضعف نتيجتها

توجد توصيات أُرسلت فعلًا واجتازت القواعد، منها ALT وETHFI وFET وBONK؛ نجاح الإرسال أو الدرجة العالية لا يثبت حركة لاحقة أو تعبئة حد الدخول. [ALT](https://github.com/mstafa0932/spot-scanner/blob/f54320311d818d7cc0f41dacb3b64364c54055f7/monitoring/cases/ALT_TL_20261019.md) مثلًا أُرسلت بدرجة89، لكن حد الدخول انتهى بلا تعبئة موثقة رغم صور السعر الأعلى. الأهداف المشتقة من الوقف وR:R ليست توقعًا مستقلًا بأن السوق سيصل إليها خلال6ساعات.

لذلك توجد مهمتان منفصلتان للاختبار: جودة الاختيار والتوقيت، وسلامة تحويل أهلية READY إلى إرسال. لم تثبت هذه المراجعة عطلًا جديدًا أضاع توصية مستوفية كل البوابات. إصلاح الشموع الأصلية وسياسة EARLY WATCH العام كانا عملين سابقين موثقين؛ لا نعيد تسميتهما حلًا جديدًا لفجوة الاختيار.

## صورة عامة من جميع الأسواق، لا من الصور الصاعدة وحدها

على **577,291 مشاهدة سوق/دورة**: السيولة225405، السعة152446، الدفتر118026، رفض discovery64777، البيانات14275، trigger2194، المخاطرة73، cooldown60، execution23، ومرحلةshadow12. أول ثلاث مجموعات تمثل **85.90%** من المشاهدات وتتوقف قبل المؤشرات الفنية.

هذه تكرارات دورات، لا أعداد فرص أو صفقات أو رسائل؛ وتتضمن حقب كود مختلفة خلال الفترة. لا تستنتج منها نسبة نجاح ولا أن كل تأجيل سعة فشل. في السجل المدمج كان أقصى تأجيل متتالٍ مرصود لـOP/XPL/HOT/BEAM دورتين وTLM/SPK دورة واحدة؛ هذا لا يعيد بناء دفاتر الفترات غير المرصودة.

## الاختبار البحثي المجمد والنتيجة التي لا نحذفها

استؤنف البرنامج الأصلي دون تعديل على **86لقطة جديدة** فقط منذ30أكتوبر16:14. قُرئت95حالة وحُفظت686هوية حلقة بحثية، تتضمن معلقات من الدفعة السابقة. لا تجمع أعداد الدفعات قبل دمج الهويات.

| القاعدة، أفق6h | نتائج مرصودة | ارتفاع آخر سعر≥5% | بلا هذا الارتفاع | مجهول/غير ناضج |
| --- | ---: | ---: | ---: | ---: |
| hot_once | 301 | 23 | 278 | 98 |
| persistent_heat | 111 | 9 | 102 | 37 |
| compression_heat | 104 | 7 | 97 | 35 |

هذه نتائج أسعار مأخوذة من عينات، وليست صفقات أو ربحًا أو إنذارات حية. المقارنة متداخلة ولا تثبت تفوقًا إحصائيًا. بقيت بصمة البرنامج `fef977da9b6f9c8157942e8cd4c29a0d6cfa544ca990c3a187dd5c63013d3c36` وبصمة البروتوكول `2d4bb6c7d63b1aabc18a07a51b06d306f5aaaa495c0a9c1c4938f2b47486de74`. الاستئناف المحلي الناجح لا يصلح اتصال بيئة المهمة المجدولة تلقائيًا. أحدث [نقطة بحثية](early-surge-continuation-20261031-0640.json) تنتهي31أكتوبر06:36:31.

## ما يلزم للإصلاح القابل للاختبار، دون تنفيذه الآن

1. **مسار ضياع أهلية مثبتة:** لأي ادعاءREADY ضائعة، نجمع كل بوابات وقت القرار ومرحلة دفتر الأسعار الجديد والمخاطرة وقيود الإرسال وردTelegram. إن اجتازت جميعًا ولم تُرسل، يُصاغ اختبار انحدار يفشل على تلك الحالة ثم أصغر إصلاح. لا نملك حالة جديدة مستوفية ذلك هنا.
2. **مسار توقيت النموذج:** هدف رصد الاستعداد قبل الاندفاع يحتاج اختبار معلومات تسبق الحركة من شموع Paribu الأصلية وحجمها، مع الاتجاه الأعلى ودفتر وقت القرار. قواعد نشاط24h أو الدرجة الحالية وحدها لم تثبت ذلك. تُجمّد فرضية منفصلة قبل تقييم بيانات لاحقة تشمل غير الصاعدة، وتُقاس الأسبقية والإنذارات الخاطئة والحالات التي بقيت غير قابلة للدخول. لا نرفع RSI للسماح بمطاردة الحركة، ولا نحولHOT إلىREADY.
3. **مدخلات ناقصة:** السجلات تتوقف عند أول مانع، وبعض الشموع الخام وعمقL2 التاريخي غير محفوظ. اقتراح جمع الحد الأدنى المؤرخ اللازم يُعرض على وكيل الإصلاح لاحقًا؛ لا يُضاف جامع أو كود تشغيل أثناء أسبوع القراءة فقط. لا نملأ الناقص من سعر لاحق.
4. **اعتماد الأداء:** تحسن وقت الإنذار يجب أن يصمد خارج الصور المختارة، مع بقاء قيود التنفيذ والمخاطرة والرسوم والانزلاق. لا تكفي صحة الفلاتر لإثبات جودة الاختيار، ولا كثرة الصور لإثبات أن فتحها مربح.

## فهرس المتابعة الموحّد

الأسباب أدناه أكبر ثلاثة أسباب محفوظة لكل رمز في2493قراءة، وليست قائمة موانع متزامنة أو حكمًا بأن الرمز انفجر. الزوجانUSDT وسياقBTC والمقارنة المشروطة يبقيان موسومين بوضوح فيJSON. الصور المعاد إرسالها مرتبطة بسجلها السابق، ولا تُحتسب فرصًا جديدة.

| السوق/السياق | قرارات محفوظة | أكثر أسباب/نتائج السجل تكرارًا |
| --- | ---: | --- |
| ALT_TL | 2493 | imbalance_too_low: 825; not_evaluated_capacity: 648; spread_too_high: 272 |
| ANIME_TL | 2493 | quote_volume_below_minimum: 1027; spread_too_high: 821; not_evaluated_capacity: 645 |
| ARB_TL | 2493 | not_evaluated_capacity: 1053; imbalance_too_low: 407; RSI outside watch range: 248 |
| ATH_TL | 2493 | not_evaluated_capacity: 892; quote_volume_below_minimum: 477; imbalance_too_low: 465 |
| BEAM_TL | 2493 | not_evaluated_capacity: 1061; spread_too_high: 570; imbalance_too_low: 450 |
| BONK_TL | 2493 | not_evaluated_capacity: 1066; RSI outside watch range: 320; imbalance_too_low: 219 |
| BTC_TL | 2493 | excluded_base_asset: 2493 |
| CFG_TL (مقارنة مشروطة لصورة CF) | 2493 | quote_volume_below_minimum: 2156; not_evaluated_capacity: 159; spread_too_high: 84 |
| CTSI_TL | 2493 | quote_volume_below_minimum: 1040; not_evaluated_capacity: 696; imbalance_too_low: 680 |
| DOGE_TL | 2493 | not_evaluated_capacity: 1061; candle_error:synthetic_candle_limit_exceeded: 281; RSI outside watch range: 280 |
| DYM_TL | 2493 | not_evaluated_capacity: 1117; spread_too_high: 835; imbalance_too_low: 277 |
| ETHFI_TL | 2493 | not_evaluated_capacity: 677; RSI outside watch range: 502; imbalance_too_low: 277 |
| FET_TL | 2493 | not_evaluated_capacity: 1126; imbalance_too_low: 300; 4h clearly weak: 283 |
| GRASS_TL | 2493 | not_evaluated_capacity: 1064; imbalance_too_low: 719; RSI outside watch range: 216 |
| HOT_TL | 2493 | not_evaluated_capacity: 1124; RSI outside watch range: 309; candle_error:synthetic_candle_limit_exceeded: 298 |
| IMX_TL | 2493 | quote_volume_below_minimum: 2493 |
| INJ_TL | 2493 | not_evaluated_capacity: 889; quote_volume_below_minimum: 550; candle_error:synthetic_candle_limit_exceeded: 267 |
| LIT_TL | 2493 | quote_volume_below_minimum: 2107; not_evaluated_capacity: 192; RSI outside watch range: 59 |
| MASK_TL | 2493 | quote_volume_below_minimum: 2328; not_evaluated_capacity: 73; RSI outside watch range: 25 |
| MEGA_TL | 2493 | quote_volume_below_minimum: 1784; not_evaluated_capacity: 321; imbalance_too_low: 118 |
| MINA_TL | 2493 | not_evaluated_capacity: 1137; spread_too_high: 475; imbalance_too_low: 394 |
| MORPHO_USDT | 0 | لا سجل TL مطابق |
| NEAR_TL | 2493 | not_evaluated_capacity: 1078; imbalance_too_low: 470; spread_too_high: 222 |
| NMR_TL | 2493 | imbalance_too_low: 977; not_evaluated_capacity: 690; 4h clearly weak: 395 |
| OGN_TL | 2493 | not_evaluated_capacity: 990; imbalance_too_low: 903; RSI outside watch range: 135 |
| ONDO_TL | 2493 | quote_volume_below_minimum: 1118; not_evaluated_capacity: 653; imbalance_too_low: 288 |
| OP_TL | 2493 | quote_volume_below_minimum: 1831; not_evaluated_capacity: 331; candle_error:synthetic_candle_limit_exceeded: 147 |
| PEPE_TL | 2493 | not_evaluated_capacity: 740; imbalance_too_low: 578; RSI outside watch range: 347 |
| PUMP_TL | 2493 | not_evaluated_capacity: 730; imbalance_too_low: 496; 4h clearly weak: 328 |
| RAD_TL | 2493 | not_evaluated_capacity: 1119; spread_too_high: 1091; imbalance_too_low: 121 |
| REEF_TL | 2493 | quote_volume_below_minimum: 1064; not_evaluated_capacity: 683; spread_too_high: 654 |
| RLC_TL | 2493 | not_evaluated_capacity: 1093; imbalance_too_low: 1073; RSI outside watch range: 111 |
| RVN_TL | 2493 | not_evaluated_capacity: 897; quote_volume_below_minimum: 602; imbalance_too_low: 450 |
| SAFA_TL | 2493 | not_evaluated_capacity: 1156; spread_too_high: 815; imbalance_too_low: 363 |
| SAND_TL | 2493 | not_evaluated_capacity: 812; imbalance_too_low: 582; quote_volume_below_minimum: 541 |
| SENT_TL | 2493 | quote_volume_below_minimum: 1389; not_evaluated_capacity: 449; imbalance_too_low: 354 |
| SFA_TL | 2493 | not_evaluated_capacity: 1227; spread_too_high: 1022; imbalance_too_low: 134 |
| SOL_TL | 2493 | not_evaluated_capacity: 1126; RSI outside watch range: 338; candle_error:synthetic_candle_limit_exceeded: 266 |
| SPK_TL | 2493 | imbalance_too_low: 911; not_evaluated_capacity: 633; RSI outside watch range: 329 |
| TIA_TL | 2493 | not_evaluated_capacity: 1070; imbalance_too_low: 893; synthetic_candles_present: 210 |
| TLM_TL | 2493 | not_evaluated_capacity: 1125; imbalance_too_low: 778; RSI outside watch range: 209 |
| TRX_USDT | 0 | لا سجل TL مطابق |
| T_TL | 2493 | not_evaluated_capacity: 994; imbalance_too_low: 722; quote_volume_below_minimum: 282 |
| VVV_TL | 2493 | quote_volume_below_minimum: 1667; not_evaluated_capacity: 363; imbalance_too_low: 182 |
| WLD_TL | 2493 | not_evaluated_capacity: 1085; imbalance_too_low: 538; RSI outside watch range: 295 |
| W_TL | 2493 | not_evaluated_capacity: 1093; imbalance_too_low: 553; RSI outside watch range: 325 |
| XCN_TL | 2493 | not_evaluated_capacity: 1070; spread_too_high: 573; imbalance_too_low: 437 |
| XPL_TL | 2493 | quote_volume_below_minimum: 2099; not_evaluated_capacity: 207; spread_too_high: 112 |
| XTZ_TL | 2493 | not_evaluated_capacity: 1161; spread_too_high: 324; RSI outside watch range: 253 |
| ZRO_TL | 2493 | not_evaluated_capacity: 1014; imbalance_too_low: 501; 4h clearly weak: 302 |
