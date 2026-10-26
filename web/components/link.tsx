import NextLink from "next/link";
import type { ComponentProps } from "react";

/**
 * next/link without prefetching. The desktop app serves a static export, which
 * has no route payloads to prefetch: each prefetch was a failed request holding
 * one of the six connections a browser allows per site. Pages load on click.
 */
export default function Link(props: ComponentProps<typeof NextLink>) {
  return <NextLink prefetch={false} {...props} />;
}
