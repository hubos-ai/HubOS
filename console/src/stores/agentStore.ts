import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { AgentSummary } from "../api/types/agents";

interface AgentStore {
  selectedAgent: string;
  agents: AgentSummary[];
  setSelectedAgent: (agentId: string) => void;
  setAgents: (agents: AgentSummary[]) => void;
  addAgent: (agent: AgentSummary) => void;
  removeAgent: (agentId: string) => void;
  updateAgent: (agentId: string, updates: Partial<AgentSummary>) => void;
}

export const useAgentStore = create<AgentStore>()(
  persist(
    (set) => ({
      selectedAgent: "default",
      agents: [],

      setSelectedAgent: (agentId) => set({ selectedAgent: agentId }),

      setAgents: (agents) => set({ agents }),

      addAgent: (agent) =>
        set((state) => ({
          agents: [...state.agents, agent],
        })),

      removeAgent: (agentId) =>
        set((state) => ({
          agents: state.agents.filter((a) => a.id !== agentId),
          ...(state.selectedAgent === agentId
            ? { selectedAgent: "default" }
            : {}),
        })),

      updateAgent: (agentId, updates) =>
        set((state) => ({
          agents: state.agents.map((a) =>
            a.id === agentId ? { ...a, ...updates } : a,
          ),
        })),
    }),
    {
      name: "hubos-agent-storage",
      storage: {
        getItem: (name) => {
          try {
            const value = sessionStorage.getItem(name);
            return value ? JSON.parse(value) : null;
          } catch (error) {
            console.error(`Failed to parse agent storage "${name}":`, error);
            // Remove corrupted data to prevent repeated errors
            sessionStorage.removeItem(name);
            return null;
          }
        },
        setItem: (() => {
          const timers = new Map<string, ReturnType<typeof setTimeout>>();
          return (name: string, value: unknown) => {
            const existing = timers.get(name);
            if (existing) clearTimeout(existing);
            timers.set(
              name,
              setTimeout(() => {
                timers.delete(name);
                try {
                  sessionStorage.setItem(name, JSON.stringify(value));
                } catch (error) {
                  console.error(
                    `Failed to save agent storage "${name}":`,
                    error,
                  );
                }
              }, 100),
            );
          };
        })(),
        removeItem: (name) => {
          sessionStorage.removeItem(name);
        },
      },
    },
  ),
);
