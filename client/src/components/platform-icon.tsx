import { Braces, Globe2, Mail, MonitorCog, type LucideIcon } from "lucide-react"
import aws from "../assets/platforms/aws.png"
import github from "../assets/platforms/github.svg"
import gmail from "../assets/platforms/gmail.png"
import netsuite from "../assets/platforms/netsuite.png"
import railway from "../assets/platforms/railway.png"
import render from "../assets/platforms/render.svg"
import resend from "../assets/platforms/resend.png"
import segment from "../assets/platforms/segment.svg"
import sendgrid from "../assets/platforms/sendgrid.png"
import snowflake from "../assets/platforms/snowflake.png"
import stripe from "../assets/platforms/stripe.svg"
import vercel from "../assets/platforms/vercel.png"

const marks = new Map<string, string | LucideIcon>([
  ["email", Mail],
  ["aws", aws],
  ["github", github],
  ["gmail", gmail],
  ["kinu", "/favicon.svg"],
  ["netsuite", netsuite],
  ["railway", railway],
  ["render", render],
  ["resend", resend],
  ["segment", segment],
  ["sendgrid", sendgrid],
  ["snowflake", snowflake],
  ["stripe", stripe],
  ["vercel", vercel],
  ["custom-api", Braces],
  ["computer-use", MonitorCog],
])

export function PlatformIcon({ platform, size = 20 }: { platform: string; size?: number }) {
  const mark = marks.get(platform.toLowerCase()) ?? Globe2
  if (typeof mark === "string") {
    return <img src={mark} width={size} height={size} alt="" className="shrink-0 object-contain" />
  }
  const Glyph = mark
  return <Glyph size={size} strokeWidth={1.7} className="shrink-0 text-[var(--ink-soft)]" aria-hidden="true" />
}
