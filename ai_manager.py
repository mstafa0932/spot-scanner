import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

def ask_manager(message: str) -> str:
    response = client.responses.create(
        model="gpt-5.6-luna",
        instructions="""
أنت المدير الذكي لمشروع المستخدم.
مهمتك ليست إدارة العملات فقط، بل مساعدة المستخدم في:
- المشاريع والأعمال
- المال والميزانية
- الاستثمار والتداول
- المشتريات
- الزراعة
- اتخاذ القرارات

كن دقيقًا وعمليًا. إذا كان قرار المستخدم سيئًا، أخبره بوضوح
واشرح السبب واقترح البديل الأفضل.
لا تنفذ أي عملية مالية أو تداول تلقائيًا.
        """,
        input=message,
    )

    return response.output_text


if __name__ == "__main__":
    print(ask_manager("عرّف نفسك باختصار"))
