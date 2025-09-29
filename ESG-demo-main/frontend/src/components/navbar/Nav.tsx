"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { MdLogout, MdSettings, MdPerson } from "react-icons/md";
import { Button } from "@/components/ui/button";
import EulerLogo from "@/assets/Euler-Img.svg";
import Image from "next/image";

export default function Nav({ className }: { className?: string }) {
  const getInitials = () => {
    return "U";
  };
  return (
    <nav
      className={`shadow-md border-b border-gray-300 px-6 py-3 flex items-center justify-between w-full bg-white ${className}`}>
      <div className="flex items-center gap-4">
        <Image
          src={EulerLogo}
          alt="Euler Logo"
          className="w-8 sm:w-10 h-auto"
        />
        <div className="flex items-center gap-3">
          <h1 className="!text-base sm:!text-xl font-semibold text-[#2274BC]">
            ESG Report Analysis
          </h1>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden sm:flex items-center space-x-2">
          <span className="text-[#2274BC]">Welcome,</span>
          <span className="font-medium text-primary">John Doe</span>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="rounded-full h-10 w-10 sm:h-12 sm:w-12 p-0 cursor-pointer"
              aria-label="User menu">
              <Avatar className="h-8 w-8">
                <AvatarFallback className="text-sm sm:text-base">
                  {getInitials()}
                </AvatarFallback>
              </Avatar>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="min-w-[150px] sm:min-w-[180px]">
            <DropdownMenuLabel className="text-sm sm:text-base">
              My Account
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-sm sm:text-base">
              <MdPerson className="mr-2" />
              <span>Profile</span>
            </DropdownMenuItem>
            <DropdownMenuItem className="text-sm sm:text-base">
              <MdSettings className="mr-2" />
              <span>Settings</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-sm sm:text-base">
              <MdLogout className="mr-2" />
              <span>Logout</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
