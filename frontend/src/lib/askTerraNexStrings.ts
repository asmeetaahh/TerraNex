import type { LanguageCode } from './i18n'

export interface AskTerraNexStrings {
  subtitle: string
  placeholder: string
  askButton: string
  listening: string
  micTooltip: string
  micUnsupported: string
  micBlocked: string
  micNoSpeech: string
  micGeneric: string
  processing: string
  previewAi: string
  responseLanguageNote: string
  speakerTooltip: string
  speakerUnsupported: string
  speaking: string
  stopSpeaking: string
  questions: {
    water: string
    heat: string
    cropHealth: string
    soil: string
  }
}

/**
 * Static, hand-authored UI copy per language — not a translation service call.
 * The response *content* is assembled from real backend data (English), so only
 * the interface chrome is localized here; `responseLanguageNote` says so plainly
 * whenever a non-English language is selected. Add a language by adding one
 * block here that matches a `LanguageCode` in `lib/i18n.ts`.
 */
export const ASK_TERRANEX_STRINGS: Record<LanguageCode, AskTerraNexStrings> = {
  en: {
    subtitle: "Tell TerraNex what's concerning you about your farm.",
    placeholder: 'e.g. My crops are looking weak and the leaves are turning yellow...',
    askButton: 'Ask TerraNex',
    listening: 'Listening — speak now',
    micTooltip: 'Voice input (preview)',
    micUnsupported: 'Voice input preview — not available in this browser',
    micBlocked: 'Microphone access was blocked — allow it in your browser to use voice input.',
    micNoSpeech: "Didn't catch that — try again.",
    micGeneric: 'Voice input is unavailable right now.',
    processing: 'Thinking…',
    previewAi: 'Preview AI response',
    responseLanguageNote: 'Response content is shown in English for now.',
    speakerTooltip: 'Read response aloud',
    speakerUnsupported: "Read-aloud isn't available in this browser",
    speaking: 'Speaking…',
    stopSpeaking: 'Stop',
    questions: {
      water: 'Why is my water risk severe?',
      heat: 'What should I do about the heat stress?',
      cropHealth: 'Why is my crop health low?',
      soil: 'How can I improve my soil?',
    },
  },
  hi: {
    subtitle: 'टेराNex को बताएं कि आपके खेत को लेकर क्या चिंता है।',
    placeholder: 'जैसे: मेरी फ़सल कमज़ोर लग रही है और पत्तियाँ पीली हो रही हैं...',
    askButton: 'टेराNex से पूछें',
    listening: 'सुन रहा हूँ — बोलिए',
    micTooltip: 'आवाज़ इनपुट (प्रीव्यू)',
    micUnsupported: 'आवाज़ इनपुट प्रीव्यू — इस ब्राउज़र में उपलब्ध नहीं है',
    micBlocked: 'माइक्रोफ़ोन एक्सेस ब्लॉक हो गया — आवाज़ इनपुट के लिए इसे ब्राउज़र में अनुमति दें।',
    micNoSpeech: 'कुछ सुनाई नहीं दिया — फिर से कोशिश करें।',
    micGeneric: 'आवाज़ इनपुट अभी उपलब्ध नहीं है।',
    processing: 'सोच रहा हूँ…',
    previewAi: 'प्रीव्यू AI जवाब',
    responseLanguageNote: 'जवाब की सामग्री अभी अंग्रेज़ी में दिखाई जा रही है।',
    speakerTooltip: 'जवाब सुनें',
    speakerUnsupported: 'इस ब्राउज़र में जवाब सुनने की सुविधा उपलब्ध नहीं है',
    speaking: 'बोल रहा हूँ…',
    stopSpeaking: 'रोकें',
    questions: {
      water: 'मेरा जल जोखिम गंभीर क्यों है?',
      heat: 'गर्मी के तनाव के लिए मुझे क्या करना चाहिए?',
      cropHealth: 'मेरी फ़सल का स्वास्थ्य कम क्यों है?',
      soil: 'मैं अपनी मिट्टी को कैसे बेहतर बना सकता हूँ?',
    },
  },
  pt: {
    subtitle: 'Diga à TerraNex o que está preocupando você na sua fazenda.',
    placeholder: 'ex: Minhas plantas parecem fracas e as folhas estão amarelando...',
    askButton: 'Perguntar à TerraNex',
    listening: 'Ouvindo — pode falar',
    micTooltip: 'Entrada por voz (pré-visualização)',
    micUnsupported: 'Pré-visualização de voz — não disponível neste navegador',
    micBlocked: 'O acesso ao microfone foi bloqueado — permita-o no navegador para usar a entrada por voz.',
    micNoSpeech: 'Não entendi — tente novamente.',
    micGeneric: 'A entrada por voz não está disponível no momento.',
    processing: 'Pensando…',
    previewAi: 'Resposta de IA (pré-visualização)',
    responseLanguageNote: 'O conteúdo da resposta é exibido em inglês por enquanto.',
    speakerTooltip: 'Ouvir a resposta',
    speakerUnsupported: 'Leitura em voz alta não disponível neste navegador',
    speaking: 'Falando…',
    stopSpeaking: 'Parar',
    questions: {
      water: 'Por que meu risco de água é severo?',
      heat: 'O que devo fazer sobre o estresse térmico?',
      cropHealth: 'Por que a saúde da minha lavoura está baixa?',
      soil: 'Como posso melhorar meu solo?',
    },
  },
  ru: {
    subtitle: 'Расскажите TerraNex, что вас беспокоит на ферме.',
    placeholder: 'например: Мои растения выглядят слабыми, а листья желтеют...',
    askButton: 'Спросить TerraNex',
    listening: 'Слушаю — говорите',
    micTooltip: 'Голосовой ввод (превью)',
    micUnsupported: 'Голосовой ввод — не поддерживается в этом браузере',
    micBlocked: 'Доступ к микрофону заблокирован — разрешите его в браузере для голосового ввода.',
    micNoSpeech: 'Ничего не расслышал — попробуйте ещё раз.',
    micGeneric: 'Голосовой ввод сейчас недоступен.',
    processing: 'Думаю…',
    previewAi: 'Превью-ответ ИИ',
    responseLanguageNote: 'Содержание ответа пока отображается на английском языке.',
    speakerTooltip: 'Прослушать ответ',
    speakerUnsupported: 'Озвучивание недоступно в этом браузере',
    speaking: 'Говорю…',
    stopSpeaking: 'Стоп',
    questions: {
      water: 'Почему мой риск нехватки воды критический?',
      heat: 'Что делать при тепловом стрессе?',
      cropHealth: 'Почему здоровье моей культуры низкое?',
      soil: 'Как улучшить почву?',
    },
  },
  zh: {
    subtitle: '告诉 TerraNex 您对农场的担忧。',
    placeholder: '例如：我的作物看起来很弱，叶子在变黄……',
    askButton: '询问 TerraNex',
    listening: '正在聆听 — 请说话',
    micTooltip: '语音输入（预览）',
    micUnsupported: '语音输入预览 — 此浏览器不支持',
    micBlocked: '麦克风访问被阻止 — 请在浏览器中允许，以使用语音输入。',
    micNoSpeech: '没有听清 — 请再试一次。',
    micGeneric: '语音输入暂不可用。',
    processing: '思考中…',
    previewAi: '预览版 AI 回复',
    responseLanguageNote: '回复内容目前以英文显示。',
    speakerTooltip: '朗读回复',
    speakerUnsupported: '此浏览器不支持朗读功能',
    speaking: '正在朗读…',
    stopSpeaking: '停止',
    questions: {
      water: '为什么我的缺水风险很严重？',
      heat: '面对高温胁迫我该怎么做？',
      cropHealth: '为什么我的作物健康状况偏低？',
      soil: '我该如何改良土壤？',
    },
  },
  ar: {
    subtitle: 'أخبر TerraNex بما يقلقك بشأن مزرعتك.',
    placeholder: 'مثال: محاصيلي تبدو ضعيفة والأوراق تصفرّ...',
    askButton: 'اسأل TerraNex',
    listening: 'أستمع — تفضل بالحديث',
    micTooltip: 'الإدخال الصوتي (معاينة)',
    micUnsupported: 'معاينة الإدخال الصوتي — غير متاحة في هذا المتصفح',
    micBlocked: 'تم حظر الوصول إلى الميكروفون — يرجى السماح به في المتصفح لاستخدام الإدخال الصوتي.',
    micNoSpeech: 'لم أسمع جيدًا — حاول مرة أخرى.',
    micGeneric: 'الإدخال الصوتي غير متاح حاليًا.',
    processing: 'جارٍ التفكير…',
    previewAi: 'معاينة رد الذكاء الاصطناعي',
    responseLanguageNote: 'محتوى الرد يُعرض حاليًا باللغة الإنجليزية.',
    speakerTooltip: 'استماع للرد',
    speakerUnsupported: 'القراءة الصوتية غير متاحة في هذا المتصفح',
    speaking: 'جارٍ القراءة…',
    stopSpeaking: 'إيقاف',
    questions: {
      water: 'لماذا خطر نقص المياه لديّ شديد؟',
      heat: 'ماذا يجب أن أفعل حيال إجهاد الحرارة؟',
      cropHealth: 'لماذا صحة المحصول لديّ منخفضة؟',
      soil: 'كيف يمكنني تحسين تربتي؟',
    },
  },
}

export function getStrings(code: LanguageCode): AskTerraNexStrings {
  return ASK_TERRANEX_STRINGS[code] ?? ASK_TERRANEX_STRINGS.en
}
