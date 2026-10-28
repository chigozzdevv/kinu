const passwords = {
  api: process.env.KINU_MONGO_API_PASSWORD,
  executor: process.env.KINU_MONGO_EXECUTOR_PASSWORD,
  native: process.env.KINU_MONGO_NATIVE_PASSWORD,
}
const admin = db.getSiblingDB("admin")
for (const [user, databases] of Object.entries({ api: ["kinu"], executor: ["kinu", "kinu_private"], native: ["kinu_agent"] })) {
  admin.createUser({ user, pwd: passwords[user], roles: databases.map((db) => ({ role: "readWrite", db })) })
}
