import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { AuthenticationBoundary } from "./root"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthenticationBoundary />
  </StrictMode>,
)
