import type { Company } from '../../api/schemas'

export const recommendedQuestions = [
  { label: '综合调查', text: '请对远景精工（合成）有限公司开展截至2026年9月11日的授信前调查，汇总经营、司法、舆情和知识产权方面的规则命中、对应证据及需要人工核实的事项。' },
  { label: '来源不足', text: '请调查海晟服务（合成）有限公司截至2026年9月11日的情况。若规则分为0，能否认为没有风险？请指出缺失或不可用的来源、影响判断的限制和下一步核查事项。' },
  { label: '司法追问', text: '请继续解释这次调查中司法规则为何计35分：展示引用原文、来源和观察日期，区分证据事实与尚待人工确认的判断，并列出下一步核查事项。' },
]

export const exampleDrafts = {
  '01': { companyCode: 'SYNTHETIC-COMPANY-003', question: recommendedQuestions[0].text },
  '02': { companyCode: 'SYNTHETIC-COMPANY-007', question: recommendedQuestions[1].text },
}

export function scopeConflict(question: string, companyId: string, date: string, companies: Company[]): string {
  const text = question.normalize('NFKC')
  if (companies.some(company => company.id !== companyId && (
    text.includes(company.name.normalize('NFKC')) ||
    text.toUpperCase().includes(company.unified_credit_code.toUpperCase())
  ))) return '问题中的企业与当前选择不同，请核对企业后再发送。'
  for (const match of text.matchAll(/(?<!\d)(\d{4})(?:年|[-/.])(\d{1,2})(?:月|[-/.])(\d{1,2})日?(?!\d)/g)) {
    const before = text.slice(0, match.index)
    const after = text.slice(match.index + match[0].length)
    if (!/(?:截至|截止(?:到|至)?|基准日|as of)(?:设为|改为|为|是)?\s*:?\s*$/i.test(before) &&
      !/^\s*(?:为基准日|为准|的(?:综合)?(?:调查|风险(?:报告|结果)))/.test(after)) continue
    if ([match[1], match[2].padStart(2, '0'), match[3].padStart(2, '0')].join('-') !== date) {
      return '问题中的基准日与当前选择不同，请核对日期后再发送。'
    }
  }
  return ''
}
