import fetch from 'node-fetch';

function foundryBaseUrl(): string {
  const host = process.env.FOUNDRY_HOST || 'localhost';
  const port = process.env.FOUNDRY_PORT || '5273';
  return `http://${host}:${port}/v1`;
}

function model(): string {
  return process.env.FOUNDRY_MODEL || 'phi-3.5-mini-instruct';
}

interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

async function chatCompletion(messages: ChatMessage[], maxTokens = 512): Promise<string> {
  const url = `${foundryBaseUrl()}/chat/completions`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: model(), messages, max_tokens: maxTokens, temperature: 0.3 }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Foundry Local returned ${res.status}: ${text}`);
  }

  const json = (await res.json()) as {
    choices: { message: { content: string } }[];
  };
  return json.choices[0]?.message?.content ?? '';
}

export async function analyzeMarket(symbol: string, priceData: unknown): Promise<string> {
  return chatCompletion([
    {
      role: 'system',
      content:
        'You are a quantitative trading analyst. Provide concise, data-driven market analysis. Focus on key levels, trend direction, and actionable insights. Keep responses under 200 words.',
    },
    {
      role: 'user',
      content: `Analyze this market data for ${symbol}:\n${JSON.stringify(priceData, null, 2)}\n\nProvide: trend direction, key support/resistance levels, and a brief recommendation.`,
    },
  ]);
}

export async function analyzeRisk(
  portfolio: unknown,
  proposedTrade: unknown
): Promise<string> {
  return chatCompletion([
    {
      role: 'system',
      content:
        'You are a risk management specialist. Evaluate trading risk with focus on position sizing, drawdown potential, and portfolio correlation. Keep responses under 200 words.',
    },
    {
      role: 'user',
      content: `Portfolio:\n${JSON.stringify(portfolio, null, 2)}\n\nProposed trade:\n${JSON.stringify(proposedTrade, null, 2)}\n\nAssess the risk and provide a risk rating (1-10) with justification.`,
    },
  ]);
}

export async function generateSignal(
  symbol: string,
  indicators: unknown
): Promise<string> {
  return chatCompletion([
    {
      role: 'system',
      content:
        'You are a technical analysis AI. Generate clear BUY/SELL/HOLD signals based on provided indicators. Be specific about entry, stop-loss, and target levels.',
    },
    {
      role: 'user',
      content: `Symbol: ${symbol}\nIndicators:\n${JSON.stringify(indicators, null, 2)}\n\nGenerate a trading signal with entry price, stop-loss, and take-profit levels.`,
    },
  ]);
}

export async function healthCheck(): Promise<{ available: boolean; model: string; url: string }> {
  const url = `${foundryBaseUrl()}/models`;
  try {
    const res = await fetch(url, { method: 'GET' });
    return { available: res.ok, model: model(), url: foundryBaseUrl() };
  } catch {
    return { available: false, model: model(), url: foundryBaseUrl() };
  }
}
