// Shapes of the app's responses (app/services, app/domain). Money stays a string: the UI never parses it.
export type Role = "organizer" | "editor" | "viewer";

export interface Trip {
  id: string;
  destinations: string[];
  startsOn: string;
  endsOn: string;
  budgetUsd: string;
  travelers: number;
  version: number;
  role: Role | null;
  participants: { userId: string; role: Role }[];
}

export interface Offer {
  id: string;
  providerId: string;
  category: string;
  title: string;
  description: string;
  destination: string;
  origin?: string;
  startsAt: string;
  endsAt: string;
  travelers: number;
  price: { amount: string; currency: string };
  revision: string;
  available: boolean;
  validUntil: string;
  restrictions?: string[];
}

export interface Quote {
  offer: Offer;
  priceKind: "estimate" | "confirmed";
  expired: boolean;
  bookable: boolean;
}

export type BookingState = "pending" | "confirmed" | "failed" | "rejected" | "unknown";

export interface Attempt {
  id: string;
  itemId: string;
  attempt: number;
  state: BookingState;
  nextStep: string;
  createdAt: string;
  lastCheckedAt?: string;
  /** The provider's Booking, present once it has one. A confirmed item is shown at its accepted price. */
  booking?: { offer: Offer };
}

export interface Item extends Quote {
  id: string;
  position: number;
  booking?: Attempt;
}

export interface Itinerary {
  tripId: string;
  version: number;
  items: Item[];
  issues: { code: string; message: string; itemIds: string[] }[];
  rules: string[];
  budget: {
    currency: string;
    lines: { itemId: string; title: string; category: string; amountUsd: string; priceKind: string }[];
    estimatedUsd: string;
    confirmedUsd: string;
    totalUsd: string;
    budgetUsd: string;
    differenceUsd: string;
    overBudget: boolean;
    roundingRule: string;
  };
}

export interface Leg {
  destination: string;
  status: "complete" | "partial" | "empty" | "failed";
  options: Quote[];
  warnings: { providerId?: string; code?: string; message: string }[];
  searchedAt?: string;
  error?: { code: string; message: string; retryAfterSeconds?: number };
}

export interface Doc {
  id: string;
  tripVersion: number;
  state: "waiting" | "in_progress" | "complete" | "failed";
  requestedAt: string;
  attempts: number;
  error?: string;
  nextStep: string;
}

export interface AssistantAnswer {
  tripId: string;
  mode: string;
  answer: string;
  toolCalls: { name: string; arguments: unknown; isError: boolean }[];
}
