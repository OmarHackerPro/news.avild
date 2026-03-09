
import { Cluster, FeedItem } from "../types";

export const mockFeedItems: FeedItem[] = [
  {
    id: "cluster-1",
    rank: 1,
    title: "AI agents reshape enterprise workflows",
    description:
      "Major companies roll out autonomous AI agents to handle complex internal operations and knowledge work.",
    score: 98,
    sourceCount: 37,
    timestamp: new Date().toISOString(),
    category: "AI"
  },
  {
    id: "cluster-2",
    rank: 2,
    title: "Chipmakers race to 2nm manufacturing",
    description:
      "Semiconductor giants accelerate roadmaps as demand for AI accelerators and edge compute continues to surge.",
    score: 91,
    sourceCount: 22,
    timestamp: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
    category: "Semiconductors"
  },
  {
    id: "cluster-3",
    rank: 3,
    title: "Regulators move on AI safety standards",
    description:
      "New global frameworks emerge to govern high‑risk AI systems and model transparency.",
    score: 87,
    sourceCount: 18,
    timestamp: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
    category: "Policy"
  }
];

export const mockCluster: Cluster = {
  id: "cluster-1",
  title: "AI agents reshape enterprise workflows",
  summary:
    "Enterprises are rapidly adopting autonomous AI agents to orchestrate complex workflows, augment knowledge workers, and streamline decision‑making across departments.",
  totalSources: 37,
  totalScore: 98,
  timeRange: "Last 24 hours",
  overview:
    "Over the past day, multiple Fortune 500 companies and SaaS vendors have announced production deployments of AI agents that can plan, execute, and monitor multi‑step workflows. These systems are being integrated into ticketing, customer success, ops, and internal tools.",
  keyPoints: [
    "Leading CRM and support platforms now ship native AI agent capabilities.",
    "Early adopters report significant reduction in manual triage and routing.",
    "Vendors emphasize human‑in‑the‑loop controls and auditability.",
    "Ecosystem of third‑party agent frameworks matures with enterprise‑grade tooling."
  ],
  timeline: [
    {
      time: "08:15",
      label: "Flagship launch",
      description:
        "Major SaaS provider launches an AI agent that automates onboarding workflows end‑to‑end."
    },
    {
      time: "11:40",
      label: "Enterprise case study",
      description:
        "Global bank reports 40% reduction in internal ticket resolution times using agents."
    },
    {
      time: "15:20",
      label: "Open‑source release",
      description:
        "New open‑source orchestration layer for connecting agents to legacy systems goes GA."
    }
  ],
  relatedTopics: ["Autonomous agents", "Enterprise SaaS", "Productivity tooling", "MLOps"],
  sources: [
    {
      id: "src-1",
      title: "How AI agents are transforming enterprise operations",
      publisher: "TechSignal",
      timestamp: new Date().toISOString(),
      url: "https://example.com/ai-agents-enterprise"
    },
    {
      id: "src-2",
      title: "Inside the new wave of workflow‑native AI tools",
      publisher: "Cloud Weekly",
      timestamp: new Date(Date.now() - 1000 * 60 * 25).toISOString(),
      url: "https://example.com/workflow-ai-tools"
    },
    {
      id: "src-3",
      title: "Banks quietly adopt autonomous agents for back‑office tasks",
      publisher: "Fintech Journal",
      timestamp: new Date(Date.now() - 1000 * 60 * 75).toISOString(),
      url: "https://example.com/ai-agents-banking"
    }
  ]
};




