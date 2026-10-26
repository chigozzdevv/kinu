import { Icon } from "@iconify/react"
import algoliaIcon from "@iconify-icons/logos/algolia.js"
import anthropicIcon from "@iconify-icons/logos/anthropic-icon.js"
import cloudflareIcon from "@iconify-icons/logos/cloudflare-icon.js"
import datadogIcon from "@iconify-icons/logos/datadog-icon.js"
import githubIcon from "@iconify-icons/logos/github-icon.js"
import huggingFaceIcon from "@iconify-icons/logos/hugging-face-icon.js"
import mongodbIcon from "@iconify-icons/logos/mongodb-icon.js"
import openaiIcon from "@iconify-icons/logos/openai-icon.js"
import pineconeIcon from "@iconify-icons/logos/pinecone-icon.js"
import resendIcon from "@iconify-icons/logos/resend-icon.js"
import sendgridIcon from "@iconify-icons/logos/sendgrid-icon.js"
import sentryIcon from "@iconify-icons/logos/sentry-icon.js"
import stripeIcon from "@iconify-icons/logos/stripe.js"
import supabaseIcon from "@iconify-icons/logos/supabase-icon.js"
import twilioIcon from "@iconify-icons/logos/twilio-icon.js"
import vercelIcon from "@iconify-icons/logos/vercel-icon.js"
import type { ReactNode } from "react"

type IntegrationMark = {
  icon: ReactNode
  label: string
}

const marks: IntegrationMark[] = [
  { icon: <Icon icon={githubIcon} />, label: "GitHub" },
  { icon: <Icon icon={resendIcon} />, label: "Resend" },
  { icon: <Icon icon={openaiIcon} />, label: "OpenAI" },
  { icon: <Icon icon={anthropicIcon} />, label: "Anthropic" },
  { icon: <Icon icon={sendgridIcon} />, label: "SendGrid" },
  { icon: <Icon icon={stripeIcon} />, label: "Stripe" },
  { icon: <Icon icon={cloudflareIcon} />, label: "Cloudflare" },
  { icon: <Icon icon={twilioIcon} />, label: "Twilio" },
  { icon: <Icon icon={huggingFaceIcon} />, label: "Hugging Face" },
  { icon: <Icon icon={vercelIcon} />, label: "Vercel" },
  { icon: <Icon icon={supabaseIcon} />, label: "Supabase" },
  { icon: <Icon icon={algoliaIcon} />, label: "Algolia" },
  { icon: <Icon icon={datadogIcon} />, label: "Datadog" },
  { icon: <Icon icon={sentryIcon} />, label: "Sentry" },
  { icon: <Icon icon={pineconeIcon} />, label: "Pinecone" },
  { icon: <Icon icon={mongodbIcon} />, label: "MongoDB" },
]

const fieldMarks = [...marks, ...marks.slice(0, 6)]

export function Integrations() {
  return (
    <section id="integrations" className="landing-integrations">
      <div className="landing-integrations__field" aria-label="Kinu integrations">
        <div className="landing-integrations__marks" aria-hidden="true">
          {fieldMarks.map((mark, index) => (
            <span
              className="landing-integrations__mark"
              key={`${mark.label}-${index}`}
              style={{ animationDelay: `${-(index % marks.length) * 0.65}s` }}
              title={mark.label}
            >
              {mark.icon}
            </span>
          ))}
        </div>

        <div className="landing-integrations__copy">
          <h2>
            <span>Works with your</span>
            <span>favourite stacks.</span>
          </h2>
        </div>
      </div>
    </section>
  )
}
