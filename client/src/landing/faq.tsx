import { ChevronDown } from "lucide-react"

const questions = [
  {
    question: "Can the AI see my keys?",
    answer: "No. Kinu handles the key in an isolated executor. The agent receives a summary of the incident and the actions it’s allowed to take.",
  },
  {
    question: "What triggers a rotation?",
    answer: "You choose the triggers for each key. Kinu can act on a verified leak, an approaching expiry date, or a change from the key’s expected settings.",
  },
  {
    question: "Can I approve a rotation?",
    answer: "Yes. You can require approval before Kinu revokes the old key. After deploying and observing the replacement, Kinu waits for another workspace administrator to approve.",
  },
  {
    question: "Which providers can I connect?",
    answer: "Resend has a built-in connection. For other compatible services, configure a Custom Provider through its API or a reviewed browser playbook.",
  },
  {
    question: "How do I add my first key?",
    answer: "Open Credentials and select Add credential. Choose your service, its environment variable, and the provider that issues the key. Add a health check so Kinu can verify the replacement in your app.",
  },
]

export default function Faq() {
  return <section className="new-faq" id="faq" aria-labelledby="faq-title">
    <div className="new-faq-inner">
      <h2 id="faq-title">Frequently asked questions.</h2>
      <div className="new-faq-list">
        {questions.map(({ question, answer }) => (
          <details className="new-faq-item" name="kinu-faq" key={question}>
            <summary>{question}<ChevronDown size={17} aria-hidden="true" /></summary>
            <p>{answer}</p>
          </details>
        ))}
      </div>
    </div>
  </section>
}
