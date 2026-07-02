import { Component, type ReactNode } from "react";

const isTransientLookupError = (error: unknown): boolean =>
  error instanceof Error && /tapClient(Lookup|Resource).*out of bounds/.test(error.message);

interface Props {
  resetKey: string;
  children: ReactNode;
}

export class MessageRenderBoundary extends Component<Props, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      if (!isTransientLookupError(this.state.error)) {
        throw this.state.error;
      }

      return null;
    }

    return this.props.children;
  }
}
