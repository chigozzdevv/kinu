import { Icon } from "@iconify/react"
import anthropic from "@iconify-icons/logos/anthropic-icon.js"
import awsLambda from "@iconify-icons/logos/aws-lambda.js"
import circleci from "@iconify-icons/logos/circleci.js"
import cloudflare from "@iconify-icons/logos/cloudflare-icon.js"
import cloudinary from "@iconify-icons/logos/cloudinary-icon.js"
import contentful from "@iconify-icons/logos/contentful.js"
import datadog from "@iconify-icons/logos/datadog-icon.js"
import elasticsearch from "@iconify-icons/logos/elasticsearch.js"
import grafana from "@iconify-icons/logos/grafana.js"
import huggingFace from "@iconify-icons/logos/hugging-face-icon.js"
import linear from "@iconify-icons/logos/linear-icon.js"
import mailgun from "@iconify-icons/logos/mailgun-icon.js"
import mongodb from "@iconify-icons/logos/mongodb-icon.js"
import newRelic from "@iconify-icons/logos/new-relic-icon.js"
import openai from "@iconify-icons/logos/openai-icon.js"
import pinecone from "@iconify-icons/logos/pinecone-icon.js"
import posthog from "@iconify-icons/logos/posthog-icon.js"
import resend from "@iconify-icons/logos/resend-icon.js"
import rollbar from "@iconify-icons/logos/rollbar-icon.js"
import sendgrid from "@iconify-icons/logos/sendgrid-icon.js"
import sentry from "@iconify-icons/logos/sentry-icon.js"
import supabase from "@iconify-icons/logos/supabase-icon.js"
import twilio from "@iconify-icons/logos/twilio-icon.js"
import vercel from "@iconify-icons/logos/vercel-icon.js"
import { Braces, ChevronDown } from "lucide-react"
import { useState } from "react"
import railway from "../assets/platforms/railway.png"
import render from "../assets/platforms/render.svg"
import stripe from "../assets/platforms/stripe.svg"
import "./integrations.css"

const services = [
  { name: "Resend", mark: <Icon icon={resend} /> },
  { name: "OpenAI", mark: <Icon icon={openai} /> },
  { name: "Stripe", mark: <img src={stripe} alt="" /> },
  { name: "Supabase", mark: <Icon icon={supabase} /> },
  { name: "SendGrid", mark: <Icon icon={sendgrid} /> },
  { name: "Cloudflare", mark: <Icon icon={cloudflare} /> },
  { name: "Twilio", mark: <Icon icon={twilio} /> },
  { name: "Anthropic", mark: <Icon icon={anthropic} /> },
  { name: "Datadog", mark: <Icon icon={datadog} /> },
  { name: "Pinecone", mark: <Icon icon={pinecone} /> },
  { name: "Sentry", mark: <Icon icon={sentry} /> },
  { name: "MongoDB", mark: <Icon icon={mongodb} /> },
  { name: "Grafana", mark: <Icon icon={grafana} /> },
  { name: "Hugging Face", mark: <Icon icon={huggingFace} /> },
  { name: "Mailgun", mark: <Icon icon={mailgun} /> },
  { name: "Linear", mark: <Icon icon={linear} /> },
  { name: "Cloudinary", mark: <Icon icon={cloudinary} /> },
  { name: "New Relic", mark: <Icon icon={newRelic} /> },
  { name: "Contentful", mark: <Icon icon={contentful} /> },
  { name: "PostHog", mark: <Icon icon={posthog} /> },
  { name: "CircleCI", mark: <Icon icon={circleci} /> },
  { name: "Elasticsearch", mark: <Icon icon={elasticsearch} /> },
  { name: "Rollbar", mark: <Icon icon={rollbar} /> },
  { name: "Custom Provider", mark: <Braces strokeWidth={1.6} /> },
]

const runtimes = [
  { name: "Vercel", mark: <Icon icon={vercel} /> },
  { name: "Railway", mark: <img src={railway} alt="" /> },
  { name: "Render", mark: <img src={render} alt="" /> },
  { name: "AWS Lambda", mark: <Icon icon={awsLambda} /> },
]

function LogoList({ entries, duplicate = false }: { entries: typeof services; duplicate?: boolean }) {
  return <ul className="stack-logos" aria-hidden={duplicate || undefined}>
    {entries.map(({ name, mark }) => <li key={name}>
      <span className="stack-logo" aria-hidden="true">{mark}</span>
      {name}
    </li>)}
  </ul>
}

export default function Integrations() {
  const [showAll, setShowAll] = useState(false)

  return <section className="new-stack" id="integrations" aria-labelledby="stack-title">
    <div className="stack-directory">
      <div className="stack-group stack-services" data-expanded={showAll}>
        <div className="stack-services-header">
          <h2 id="stack-title">Works with your stack.</h2>
          <button type="button" className="stack-view-all" aria-expanded={showAll} aria-controls="stack-service-list" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show fewer services" : "View all services"} <ChevronDown aria-hidden="true" size={14} />
          </button>
        </div>
        <div className="stack-service-window" id="stack-service-list" role="group" aria-label="Services">
          <div className="stack-service-track">
            <LogoList entries={services} />
            <LogoList entries={services} duplicate />
          </div>
        </div>
      </div>
      <div className="stack-group">
        <h3>Where your apps run</h3>
        <LogoList entries={runtimes} />
      </div>
    </div>
  </section>
}
