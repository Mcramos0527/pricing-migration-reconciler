-- Creates the two empty databases used by the demo on SQL Server.
-- The tables themselves are created by:  pricing-reconciler seed
IF DB_ID('ECC47') IS NULL CREATE DATABASE ECC47;
GO
IF DB_ID('S4H') IS NULL CREATE DATABASE S4H;
GO
