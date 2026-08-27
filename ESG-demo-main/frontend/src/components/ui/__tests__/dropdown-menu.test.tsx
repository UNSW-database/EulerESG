import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

describe("DropdownMenuSubContent", () => {
  it("portals submenus outside the clipping parent menu", () => {
    const onLanguageChange = vi.fn();

    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>Account</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuSub open>
            <DropdownMenuSubTrigger>Language</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuItem onClick={() => onLanguageChange("en")}>
                English
              </DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    const parentMenu = document.querySelector<HTMLElement>(
      '[data-slot="dropdown-menu-content"]',
    );
    const languageMenu = screen.getByText("English").closest<HTMLElement>(
      '[data-slot="dropdown-menu-sub-content"]',
    );

    expect(parentMenu).not.toBeNull();
    expect(languageMenu).not.toBeNull();
    expect(parentMenu).toHaveClass(
      "overflow-y-auto",
      "overscroll-y-contain",
    );
    expect(parentMenu).not.toContainElement(languageMenu);

    fireEvent.click(screen.getByText("English"));
    expect(onLanguageChange).toHaveBeenCalledWith("en");
  });
});
